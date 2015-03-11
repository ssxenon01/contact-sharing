contact-sharing
========

**contact-sharing** is contact sharing script for gmail based on **gae-init**. It supports google apps and individual gmail accounts.

Configuration
-----------------------------------

Create client_secrets.json file from google console for your project and put it in main/ directory.

Running the Development Environment
-----------------------------------

    $ cd /path/to/project-name
    $ ./run.py -s

To test it visit `http://localhost:8080/` in your browser.

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

To watch for changes of your `*.less` & `*.coffee` files and compile them
automatically to `*.css` & `*.js` execute in another bash:

    $ ./run.py -w

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

For a complete list of commands:

    $ ./run.py -h

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

Gulp is used only for watching for changes and live reloading the page.
Install [Gulp][] as a global package:

    $ npm install -g gulp

and then from the root execute with no arguments:

    $ gulp

Deploying on Google App Engine
------------------------------

Before deploying make sure that the `app.yaml` and `config.py` are up to date
and you ran the `run.py` script to minify all the static files:

    $ ./run.py -m
    $ appcfg.py update main
