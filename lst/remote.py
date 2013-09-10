import json
import urllib, urllib2, urlparse, cookielib
import xml.etree.ElementTree as ET
import dateutil.parser

from models import Sprint
from models import JiraEntries
from models import JiraEntry
from models import ZebraDays
from models import ZebraDay
from models import ZebraEntry

class Remote(object):
    def __init__(self, base_url):
        self.base_url = base_url

    def _get_request(self, url, body = None, headers = {}):
        return urllib2.Request('%s/%s' % (self.base_url, url), body, headers)

    def _request(self, url, body = None, headers = {}):
        request = self._get_request(url, body, headers)
        opener = urllib2.build_opener()
        response = opener.open(request)
        return response

    def login(self):
        pass

    def get_data(self, url):
        pass

class JiraRemote(Remote):
    def __init__(self, base_url, username, password):
        super(JiraRemote, self).__init__(base_url)

        self.username = username
        self.password = password

    def _get_request(self, url, body = None, headers = {}):
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'LST Jira Client';
        return super(JiraRemote, self)._get_request(url, body, headers)

    def _request(self, url, body = None, headers = {}):
        request = self._get_request(url, body, headers)
        opener = urllib2.build_opener()

        try:
            response = opener.open(request)
        except urllib2.URLError:
            raise Exception('Unable to connect to Jira. Check your connection status and try again.')

        return response

    def login(self):
        pass

    def get_data(self, url):
        url = '%s&os_username=%s&os_password=%s' % (
            url,
            str(self.username),
            str(self.password)
        )

        response = self._request(url)
        response_body = response.read()

        response_xml = ET.fromstring(response_body)
        return response_xml

    def parse_story(
        self,
        response_xml,
    ):
        s = response_xml[0].findall('item')[0]
        story = {
            'project_id': s.find('project').get('id'),
            'project_name': s.find('project').text,
            'sprint_name': s.find('fixVersion').text,
        }
        return story

    def parse_stories(
        self,
        response_xml,
        nice_identifier = None,
        ignored = None,
        post_processor = None
    ):
        stories = response_xml[0].findall('item')

        jira_entries = JiraEntries()
        for s in stories:
            story = JiraEntry()
            story.id = s.find('key').text

            # check if the story should be ignored (see ignore in config)
            if ignored is not None:
                story.is_ignored = story.id in ignored
            if story.is_ignored:
                continue

            # check if the story is a 'nice to have'
            if nice_identifier is not None:
                story.is_nice = s.find('title').text.find(nice_identifier) != -1

            story.status = int(s.find('status').get('id'))
            no_sp = []
            no_bv = []
            try:
                story.business_value = float(s.find('./customfields/customfield/[@id="customfield_10064"]/customfieldvalues/customfieldvalue').text)
            except AttributeError:
                no_bv.append(story.id)
            try:
                story.story_points = float(s.find('./customfields/customfield/[@id="customfield_10040"]/customfieldvalues/customfieldvalue').text)
            except AttributeError:
                no_sp.append(story.id)
            if post_processor is not None:
                story = post_processor.post_process(story)

            jira_entries.append(story)

        if len(no_sp) > 0:
            print 'Stories ' + ', '.join(no_sp) + ' have no story points defined'

        if len(no_bv) > 0:
            print 'Stories ' + ', '.join(no_bv) + ' have no business values defined'

        return jira_entries

    def get_story_close_date(self, id, closed_status_names):
        url = "/activity?maxResults=50&streams=issue-key+IS+"
        url += str(id)
        url += '&os_username=' + str(self.username)
        url += '&os_password=' + str(self.password)

        response = self._request(url)
        response_body = response.read()

        response_xml = ET.fromstring(response_body)
        xmlns = {"atom": "http://www.w3.org/2005/Atom"}
        close_dates = []
        for name in closed_status_names:
            try:
                close_dates.append(response_xml.find("./atom:entry/atom:category/[@term='" + name + "']/../atom:published", namespaces=xmlns).text)
            except:
                pass
        if len(close_dates) == 0:
            return None

        return dateutil.parser.parse(min(close_dates))

class ZebraRemote(Remote):
    def __init__(self, base_url, username, password):
        super(ZebraRemote, self).__init__(base_url)

        self.cookiejar = cookielib.CookieJar()
        self.logged_in = False
        self.username = username
        self.password = password

    def _get_request(self, url, body = None, headers = {}):
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'LST Zebra Client';
        return super(ZebraRemote, self)._get_request(url, body, headers)

    def _request(self, url, body = None, headers = {}):
        request = self._get_request(url, body, headers)
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(self.cookiejar))

        try:
            response = opener.open(request)
        except urllib2.URLError:
            raise Exception('Unable to connect to Zebra. Check your connection status and try again.')

        self.cookiejar.extract_cookies(response, request)

        return response

    def _login(self):
        if self.logged_in:
            return

        login_url = '/login/user/%s.json' % self.username
        parameters = urllib.urlencode({
            'username': self.username,
            'password': self.password,
        })

        response = self._request(login_url, parameters)
        response_body = response.read()

        if not response.info().getheader('Content-Type').startswith('application/json'):
            self.logged_in = False
            raise Exception('Unable to login')
        else:
            self.logged_in = True

    def get_data(self, url):
        self._login()

        response = self._request(url)
        response_body = response.read()

        response_json = json.loads(response_body)
        return response_json

    def parse_users(self, response_json):
        users = response_json['command']['users']['user']
        return users

    def parse_entries(self, response_json):
        zebra_entries = []

        try:
            entries = response_json['command']['reports']['report']
        except:
            print 'No entries found in Zebra'
            return zebra_entries

        for entry in entries:
            # zebra last entries are totals, and dont have a tid
            if entry['tid'] == '':
                continue

            # parse zebra entry
            zebra_entry = self.parse_entry(entry)

            zebra_entries.append(zebra_entry)

        return zebra_entries

    def parse_entry(self, entry):
        zebra_entry = ZebraEntry()
        zebra_entry.username = str(entry['username'].encode('utf-8'))
        zebra_entry.time = float(entry['time'])
        zebra_entry.project = (entry['project'].encode('utf-8'))
        zebra_entry.date = dateutil.parser.parse(entry['date'])
        zebra_entry.id = int(entry['tid'])
        zebra_entry.description = str(entry['description'].encode('utf-8'))
        return zebra_entry

