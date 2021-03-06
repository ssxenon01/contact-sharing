# coding: utf-8
import flask
from flask import request,session,redirect,url_for
from google.appengine.api import memcache
from main import app
import re
import urllib
import model
import auth
import uuid
import copy
import pickle
import atom
import gdata.contacts.data
import gdata.contacts.client as gdc

MAX_RESULTS = 10000
SYNC_ID_TAG = 'csync-uid'

enableUpdates = True
debug = False

pvt_grp_names = set()

@app.route('/sync')
def sync():
    user_dbs, user_cursor = model.User.get_dbs()
    users = []
    new_users = []
    for user in user_dbs:
        if(user.token_blob):
            if(user.is_new):
                user.is_new=False
                user.put()
                new_users.append(user)
            else:
                users.append(user)

    if(len(new_users)>0):
        contacts = []
        for i in range(len(new_users)):
            contacts.append(UserContacts(new_users[i], createuid = False))
        ## Merge groups across all users
        AddUids(contacts)

    contacts = []
    for i in range(len(users)):
        contacts.append(UserContacts(users[i]))

    ## Merge groups across all users
    MergeGroups(users, contacts)

    pvt_groups = set()
    for i in range(len(users)):
        for grp in contacts[i].GroupIterValues():
            if grp.title.text in pvt_grp_names and grp.id:
                pvt_groups.add(grp.id.text)

    ## Merge contacts across all users
    MergeContacts(users, contacts, pvt_groups)
    return 'ok' + str(len(new_users))


################################################################################
#
# UserContacts class: manage Google server I/O and a single user's copy of
# contacts and groups.
#
################################################################################

class UserContacts(object):
    """UserContacts object loads Google contacts from an account"""

    def __init__(self, user, createuid = True):
        """Constructor for the UserContacts object.

        Takes an email and password corresponding to a gmail account.

        Args:
          email: [string] The e-mail address of the account to use for the sample.
          password: [string] The password corresponding to the account specified by
              the email parameter.
        """
        self.email = user.email

        ##
        ## Log in
        ##
        self.gd_client = gdc.ContactsClient(source='Contact Sharing')
        oauth_token = gdata.gauth.token_from_blob(user.token_blob)
        oauth_token.authorize(self.gd_client)


        # self.gd_client.ClientLogin(email, password, self.gd_client.source)

        ##
        ## Load all groups
        ##
        self.__groups = dict()
        query = gdc.ContactsQuery(max_results=10000)
        feed = self.gd_client.GetGroups(q=query)
        if feed:
            for entry in feed.entry:
                self.__groups[entry.SyncGetTag()] = entry

        ##
        ## Load all contacts
        ##
        self.__contacts = dict()
        self.nouids = set([])
        query = gdc.ContactsQuery(max_results=10000)
        feed = self.gd_client.GetContacts(q=query)
        if feed:
            for entry in feed.entry:
                # Ignore contacts without names or not belonging to any group
                if entry.group_membership_info and entry.GetPrintName():
                    # Check that contact has one and only one UID.  The
                    # test for multiple UIDs is in case Google's merge contacts
                    # combines UIDs from multiple contacts.
                    uid_obj = None
                    num_uids_found = 0
                    for obj in entry.extended_property:
                        if obj.name == SYNC_ID_TAG:
                            uid_obj = obj
                            num_uids_found += 1;

                    if not uid_obj:
                        # When syncing a new account for the first time we
                        # don't want to create uid without first checking
                        # duplicate names.  so just store these entries.
                        if not createuid:
                            self.nouids.add(entry)
                            continue

                        uid = str(uuid.uuid1())
                        uid_obj = gdata.data.ExtendedProperty(name = SYNC_ID_TAG, value = uid)
                        entry.extended_property.append(uid_obj)
                        if enableUpdates:
                            entry = self.gd_client.Update(entry)

                    if num_uids_found > 1:
                        # Drop extra UIDs
                        p = [obj for obj in entry.extended_property if obj.name != SYNC_ID_TAG]
                        p.append(uid_obj)
                        entry.extended_property = p
                        if enableUpdates:
                            entry = self.gd_client.Update(entry)

                    self.__contacts[uid_obj.value] = entry

    ##
    ## GetContact --
    ##     Fetch a single contact from local cache.
    ##
    def GetContact(self, uid):
        if uid in self.__contacts:
            return self.__contacts[uid]
        else:
            return None

    ##
    ## ContactAdd --
    ##     Add a new contact on the Google server
    ##
    def ContactAdd(self, entry):
        if enableUpdates:
            try:
                entry = self.gd_client.CreateContact(entry)
            except Exception, e:
                "can't update"
            
        self.__contacts[entry.SyncGetUID()] = entry
        return entry

    ##
    ## ContactUpdate --
    ##     Update an existing contact on the Google server
    ##
    def ContactUpdate(self, entry):
        if enableUpdates:
            try:
                entry = self.gd_client.Update(entry)
            except Exception, e:
                "can't update"
        self.__contacts[entry.SyncGetUID()] = entry
        return entry

    ##
    ## ContactDelete --
    ##     Delete a contact from the Google server
    ##
    def ContactDelete(self, entry):
        del self.__contacts[entry.SyncGetUID()]
        if enableUpdates:
            return self.gd_client.Delete(entry)

    ##
    ## ContactIterItems --
    ##     Contact object iterator
    ##
    def ContactIterItems(self):
        return self.__contacts.iteritems()

    ##
    ## ContactIterValues --
    ##     Contact object iterator
    ##
    def ContactIterValues(self):
        return self.__contacts.itervalues()

    ##
    ## GetGroup --
    ##     Fetch a single group from local cache.
    ##
    def GetGroup(self, uid):
        if uid in self.__groups:
            return self.__groups[uid]
        else:
            return None

    ##
    ## GroupChangeID --
    ##     Change the ID of a group
    ##
    def GroupChangeID(self, oldID, newID):
        entry = self.__groups[oldID]
        if entry.system_group:
            return ''

        # Update the groups dictionary
        del self.__groups[oldID]
        self.__groups[newID] = entry

        # Update the SYNC_ID_TAG in extended properties
        ext = [obj for obj in entry.extended_property if obj.name != SYNC_ID_TAG]
        ext.append(gdata.data.ExtendedProperty(name = SYNC_ID_TAG, value = newID))
        entry.extended_property = ext

        if enableUpdates:
            self.__groups[newID] = self.gd_client.Update(entry)

    ##
    ## GroupRename --
    ##     Change the name of a group
    ##
    def GroupRename(self, uid, newName):
        entry = self.__groups[uid]
        if entry.system_group:
            return ''

        entry.title = atom.data.Title(text = newName)

        # Update the groups dictionary
        self.__groups[uid] = entry

        if enableUpdates:
            self.__groups[uid] = self.gd_client.Update(entry)

    ##
    ## GroupAdd --
    ##     Add a new group on the Google server
    ##
    def GroupAdd(self, uid, name):
        entry = gdata.contacts.data.GroupEntry(title = atom.data.Title(text = name))
        entry.extended_property.append(gdata.data.ExtendedProperty(name = SYNC_ID_TAG, value = uid))

        # Update the groups dictionary
        self.__groups[uid] = entry

        if enableUpdates:
            self.__groups[uid] = self.gd_client.CreateGroup(entry)

    ##
    ## GroupIterValues --
    ##     Group object iterator
    ##
    def GroupIterValues(self):
        return self.__groups.itervalues()

    ##
    ## DumpGroups --
    ##     Dump group data and IDs.
    ##
    def DumpGroups(self):
        for id, entry in self.__groups.iteritems():
            print ' %s: %s' % (id, entry.DebugDump())


##
## Add uid to all entries, possibly using uid from entries with same name
##
def AddUids(contacts):
    # map name to (uid, account email address) for all accounts
    name2uid = dict([(ContactGetPrintName(entry), (uid, c.email)) for c in contacts for uid, entry in c.ContactIterItems()])
    for c in contacts:
        for e in c.nouids:
            # use already existing uid if possible
            if ContactGetPrintName(e) in name2uid:
                (uid, email) = name2uid[ContactGetPrintName(e)]
            else:
                uid = str(uuid.uuid1())

            uid_obj = gdata.data.ExtendedProperty(name = SYNC_ID_TAG, value = uid)
            e.extended_property.append(uid_obj)
            if enableUpdates:
                e = c.gd_client.Update(e)


################################################################################
#
# Extend Contacts class
#
################################################################################

##
## SyncGetUID class extension --
##     Return the UID of a contact or None if it doesn't have one.
##
def ContactGetUID(self):
    for obj in self.extended_property:
        if obj.name == SYNC_ID_TAG:
            return obj.value
    return None

gdata.contacts.data.ContactEntry.SyncGetUID = ContactGetUID


##
## GetPrintName class extension --
##     Return the printable name of a contact
##
def ContactGetPrintName(self):
    entry = self
    if entry.name and entry.name.full_name:
        return entry.name.full_name.text
    if entry.organization and entry.organization.name:
        return entry.organization.name.text
    return None

gdata.contacts.data.ContactEntry.GetPrintName = ContactGetPrintName


def ContactDebugDump(self):
    entry = self

    r = entry.GetPrintName() + ':\n'

    r += '  Updated: %s\n' % (entry.updated.text)

    r += '  ID: %s\n' % (entry.id.text)
    r += '  ETag: %s\n' % (entry.etag)

    for g in entry.group_membership_info:
        r += '  Group: %s\n' % (g.href)

    for cal in entry.calendar_link:
        r += '  Calendar: %s (%s)\n' % (cal.href, cal.label)

    for obj in entry.external_id:
        r += '  External ID: %s (%s)\n' % (obj.value, obj.label)

    r += '  Initials: %s\n' % (entry.initals)

    for rel in entry.relation:
        r += '  Relation: %s (%s)\n' % (rel.text, rel.rel)

    for obj in entry.user_defined_field:
        r += '  User Defined: %s (%s)\n' % (obj.value, obj.key)

    for website in entry.website:
        rel = re.sub('.*#', '', website.rel)
        r += '  Website: %s (%s)\n' % (website.href, rel)

    ## Phone
    for phone in entry.phone_number:
        p = phone.text
        if p.isdigit():
            if len(p) == 10:
                p = '(' + p[0:3] + ') ' + p[3:6] + '-' + p[6:]
            elif len(p) == 11:
                p = p[0] + ' (' + p[1:4] + ') ' + p[4:7] + '-' + p[7:]
        r += '  Phone: %s (%s)\n' % (p, re.sub('.*#', '', phone.rel))

    if entry.organization:
        r += '  Org name: \n' + entry.organization.name.text

    for obj in entry.email:
        rel = re.sub('.*#', '', obj.rel)
        r += '  E-mail: %s (%s)\n' % (obj.address, rel)

    for obj in entry.im:
        rel = re.sub('.*#', '', obj.rel)
        r += '  IM: %s (%s)\n' % (obj.address, rel)

    ## Address
    for postal_address in entry.structured_postal_address:
        if postal_address.formatted_address:
            ## Drop trailing whitespace
            addr = postal_address.formatted_address.text.rstrip('\n ')
            ## Move ZIP up
            addr = re.sub('\n([0-9]+)$', r'  \1', addr)
            ## Indent
            addr = re.sub('\n', '\n        ', addr)

            r += '  Addr: %s\n' % (addr)

    r += '  Photo link: %s\n' % (entry.GetPhotoLink().href)
    if entry.GetPhotoEditLink():
        r += '  Photo edit link: %s\n' % (entry.GetPhotoEditLink().text)

    for obj in entry.extended_property:
        r += '  Extended Property: %s (%s)\n' % (obj.value, obj.name)

    return r

gdata.contacts.data.ContactEntry.DebugDump = ContactDebugDump


################################################################################
#
# Extend GROUP class
#
################################################################################

##
## SyncGetUID class extension --
##     Return the UID of a group or None if it doesn't have one.
##
def GroupGetUID(self):
    # System group?  Use the system ID.
    if self.system_group:
        return self.system_group.id
    else:
        # Has group already been tagged with a UID
        for obj in self.extended_property:
            if obj.name == SYNC_ID_TAG:
                return obj.value
    return None

gdata.contacts.data.GroupEntry.SyncGetUID = GroupGetUID


##
## SyncGetTag class extension --
##     Return some unique tag for a group.  If a UID exists, use that.  If
##     not, use the group name.  Later program phases will guarantee all
##     groups have UIDs.
##
def GroupGetTag(self):
    tag = self.SyncGetUID()
    if not tag:
        tag = self.title.text
    return tag

gdata.contacts.data.GroupEntry.SyncGetTag = GroupGetTag


def GroupDebugDump(self):
    entry = self

    r = entry.title.text + ':\n'

    if entry.updated:
        r += '  Updated: %s\n' % (entry.updated.text)

    if entry.system_group:
        r += '  System Group: %s\n' % (entry.system_group.id)

    for obj in entry.extended_property:
        r += '  Extended Property: %s (%s)\n' % (obj.value, obj.name)

    if entry.id:
        r += '  ID: %s' % (entry.id.text)

    return r

gdata.contacts.data.GroupEntry.DebugDump = GroupDebugDump


################################################################################
#
# Combine groups from multiple accounts
#
################################################################################

def MergeGroups(users, contacts):
    ##
    ## First collect all groups that already have UIDs
    ##
    all_groups_by_uid = dict()
    for i in range(len(users)):
        for grp in contacts[i].GroupIterValues():
            uid = grp.SyncGetUID()
            if uid and ((not uid in all_groups_by_uid) or (grp.updated.text > all_groups_by_uid[uid]['updated'])):
                # First time UID is seen or this instance was updated more
                # recently.
                all_groups_by_uid[uid] = { 'name': grp.title.text,
                                           'updated': grp.updated.text }

    ##
    ## Have any groups changed names?
    ##
    for i in range(len(users)):
        for grp in contacts[i].GroupIterValues():
            uid = grp.SyncGetUID()
            if uid and (grp.title.text != all_groups_by_uid[uid]['name']):
                contacts[i].GroupRename(uid, all_groups_by_uid[uid]['name'])

    ##
    ## Build an inverse index from name to uid
    ##
    group_name_to_uid = dict()
    for uid, g in all_groups_by_uid.iteritems():
        group_name_to_uid[g['name']] = uid

    ##
    ## Collect both new and old groups, indexed by user, in preparation for making
    # groups global.
    ##
    all_groups_by_name = dict()
    for i in range(len(users)):
        for grp in contacts[i].GroupIterValues():
            name = grp.title.text

            # First time name is seen?  Initialize a list across all users.
            if not name in all_groups_by_name:
                all_groups_by_name[name] = [None] * len(users)

            all_groups_by_name[name][i] = grp

    ##
    ## Assign UIDs to new groups across all users.
    ##
    for name in all_groups_by_name.iterkeys():
        # Is there already a UID?
        if name in group_name_to_uid:
            uid = group_name_to_uid[name]
        else:
            uid = str(uuid.uuid1())

        for i in range(len(users)):
            if all_groups_by_name[name][i]:
                old_tag = all_groups_by_name[name][i].SyncGetTag()
                if old_tag != uid:
                    contacts[i].GroupChangeID(old_tag, uid)
            else:
                contacts[i].GroupAdd(uid, name)
    

################################################################################
#
# Combine contacts from multiple accounts
#
################################################################################

groupIDtoGroup = dict()

##
## MergeContacts --
##     Combine contacts from all users.
##
def MergeContacts(users, contacts, pvtGroups):
    global groupIDtoGroup

    ##
    ## Load previous state from last time the merge was run.  This is used
    ## to detect user changes to contacts.
    ##
    prev_state = [LoadPrevContactState(users[i]) for i in range(len(users))]

    ##
    ## Build a map from group HREF ID to group UID
    ##
    for i in range(len(users)):
        for grp in contacts[i].GroupIterValues():
            if grp.id:
                groupIDtoGroup[grp.id.text] = grp

    ##
    ## Find all modified contacts and replicate them
    ##
    for i in range(len(users)):
        for key, entry in contacts[i].ContactIterItems():
            # Was the contact modified since the last sync?
            name = entry.GetPrintName()
            if not key in prev_state[i]:
                modified = True
            elif entry.updated.text != prev_state[i][key]['updated']:
                modified = True
            else:
                modified = False

            # Is this contact private?
            is_private = False
            for grp in entry.group_membership_info:
                if grp.href in pvtGroups:
                    is_private = True
                    break

            for j in range(len(users)):
                if j != i:
                    c = contacts[j].GetContact(key)
                    if c:
                        if is_private:
                            contacts[j].ContactDelete(c)
                            del prev_state[j][key]
                        elif modified:
                            new_entry = UpdateContactInUser(contacts[j], entry)
                            prev_state[j][key] = {'updated': new_entry.updated.text, 'etag': new_entry.etag}
                    elif not is_private and (modified or not key in prev_state[j]):
                        new_entry = AddContactToUser(contacts[j], entry)
                        prev_state[j][key] = {'updated': new_entry.updated.text, 'etag': new_entry.etag}

    ##
    ## Look for contacts deleted by any user and delete them in all users
    ##
    for i in range(len(users)):
        for uid in prev_state[i]:
            if not contacts[i].GetContact(uid):
                for j in range(len(users)):
                    c = contacts[j].GetContact(uid)
                    if c:
                        contacts[j].ContactDelete(c)

    ##
    ## Save the last modified times of the contacts for use in the next run
    ##
    for i in range(len(users)):
        state = dict()
        for key, entry in contacts[i].ContactIterItems():
            state[key] = {'updated': entry.updated.text, 'etag': entry.etag}

        if enableUpdates:
            SaveContactState(users[i], state)


##
## AddContactToUser --
##     Take an entry from one user and add it to another user's contact list.
##
def AddContactToUser(contacts, srcEntry):
    # Make a copy so the original isn't modified
    entry = copy.copy(srcEntry)

    # Update the group membership to the target user's equivalent groups
    entry.group_membership_info = []
    for grp in srcEntry.group_membership_info:
        new_grp = contacts.GetGroup(groupIDtoGroup[grp.href].SyncGetUID())
        if new_grp.id:
            entry.group_membership_info.append(gdata.contacts.data.GroupMembershipInfo(href = new_grp.id.text))

    # Drop ID and ETag that belonged to the original user's copy
    entry.id = None
    entry.etag = None
    entry.link = None

    # Add the new contact
    return contacts.ContactAdd(entry)

##
## UpdateContactInUser --
##     Take an entry from one user and update another user's existing equivalent
##     entry.
##
def UpdateContactInUser(contacts, srcEntry):
    # Make a copy so the original isn't modified
    entry = copy.copy(srcEntry)

    # Get the current entry
    cur_entry = contacts.GetContact(srcEntry.SyncGetUID())

    # Update the group membership to the target user's equivalent groups
    entry.group_membership_info = []
    for grp in srcEntry.group_membership_info:
        new_grp = contacts.GetGroup(groupIDtoGroup[grp.href].SyncGetUID())
        if new_grp.id:
            entry.group_membership_info.append(gdata.contacts.data.GroupMembershipInfo(href = new_grp.id.text))

    # Use the ID and ETag from the current entry
    entry.id = cur_entry.id
    entry.etag = cur_entry.etag
    entry.link = cur_entry.link

    # Update the server
    return contacts.ContactUpdate(entry)


##
## LoadPrevContactState --
##     Load the state (last modified times) of contacts from the last run of
##     this program.  These times will tell us whether the user has modified
##     a contact.
##
def LoadPrevContactState(user):
    if(memcache.get(user.email)):
        return pickle.loads(memcache.get(user.email))
    else:
        return dict()
    

##
## SaveContactState --
##     Save the contact state for use in the next run.  See LoadPrevContactState().
##
def SaveContactState(user, state):
    memcache.set(user.email, pickle.dumps(state))