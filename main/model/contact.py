from google.appengine.ext import ndb
import model
import gdata.contacts.data
import atom.core

class Contact(model.Base):
  user_key = ndb.KeyProperty(kind=model.User, required=True)
  contact_id = ndb.StringProperty(default='')
  xml = ndb.TextProperty(default='')
