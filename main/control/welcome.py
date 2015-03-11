# coding: utf-8
import flask
from flask import request,session,redirect,url_for
import config
from oauth2client.client import flow_from_clientsecrets
from main import app
from google.appengine.api import memcache , app_identity
import gdata.contacts.service
import model
import auth
import crontask
import os
import pickle

###############################################################################
# Welcome
###############################################################################
@app.route('/')
def welcome():
  return flask.render_template('welcome.html', html_class='welcome')


@app.route('/connect')
def connect():
  redirect_uri = 'https://'+  app_identity.get_application_id() + '.appspot.com/oauth2callback'
  print redirect_uri
  if(os.environ['SERVER_SOFTWARE'].startswith('Development')):
    redirect_uri = 'http://localhost:8080/oauth2callback'
  flow = flow_from_clientsecrets('client_secrets.json', scope='https://www.google.com/m8/feeds', redirect_uri=redirect_uri)
  flow.params['approval_prompt'] = 'force'
  flow.params['access_type'] = 'offline'
  memcache.set(str(auth.current_user_id()), pickle.dumps(flow))

  auth_uri = flow.step1_get_authorize_url()
  return flask.redirect(auth_uri)

@app.route('/oauth2callback')
def oauth2callback():
  code = request.args.get('code')
  if code:
    flow = pickle.loads(memcache.get(str(auth.current_user_id())))
    # exchange the authorization code for user credentials
    flow.redirect_uri = request.base_url
    try:
      credentials = flow.step2_exchange(code)
      oath_token = gdata.gauth.OAuth2TokenFromCredentials(credentials)
      oath_token.scope = "https://www.google.com/m8/feeds"
      oath_token.auth_uri = 'https://accounts.google.com/o/oauth2/auth'
      oath_token.token_uri = 'https://accounts.google.com/o/oauth2/token'
      user_db = model.User.get_by_id(auth.current_user_id())
      user_db.token_blob = gdata.gauth.TokenToBlob(oath_token)
      user_db.put()
    except Exception as e:
      print "Unable to get an access token because ", e.message
      
    flask.flash('Sucessfully connected to Contact Sharing', category='success')
    user_db = model.User.get_by_id(auth.current_user_id())
    new_users = []
    if(user_db.is_new):
      new_users.append(user_db)
      user_db.is_new = False
      user_db.put()

    if(len(new_users)>0):
        contacts = []
        for i in range(len(new_users)):
            contacts.append(crontask.UserContacts(new_users[i], createuid = False))
        ## Merge groups across all users
        crontask.AddUids(contacts)

  return redirect('/')

###############################################################################
# Sitemap stuff
###############################################################################
@app.route('/sitemap.xml')
def sitemap():
  response = flask.make_response(flask.render_template(
      'sitemap.xml',
      lastmod=config.CURRENT_VERSION_DATE.strftime('%Y-%m-%d'),
    ))
  response.headers['Content-Type'] = 'application/xml'
  return response


###############################################################################
# Warmup request
###############################################################################
@app.route('/_ah/warmup')
def warmup():
  # TODO: put your warmup code here
  return 'success'
