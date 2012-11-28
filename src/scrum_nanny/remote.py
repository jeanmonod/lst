import json
import urllib, urllib2, urlparse, cookielib
import xml.etree.ElementTree as ET
import dateutil.parser

from models import Project
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

    def get_data(self, project):
        pass

class JiraRemote(Remote):
    def __init__(self, base_url, username, password):
        super(JiraRemote, self).__init__(base_url)

        self.username = username
        self.password = password

    def _get_request(self, url, body = None, headers = {}):
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'ScrumNanny Jira Client';
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

    def get_data(self, project):
        jira_project_name = project.sprint.get_jira_data('project_name')
        jira_sprint_name = project.sprint.get_jira_data('sprint_name')

        url = "/sr/jira.issueviews:searchrequest-xml/temp/SearchRequest.xml?jqlQuery=project+%3D+'" + jira_project_name + "'+and+fixVersion+%3D+'" + jira_sprint_name + "'&tempMax=1000"
        url += '&os_username=' + str(self.username)
        url += '&os_password=' + str(self.password)

        response = self._request(url)
        response_body = response.read()

        response_xml = ET.fromstring(response_body)
        stories = response_xml[0].findall('item')

        nice_identifier = project.sprint.get_jira_data('nice_identifier')
        closed_status = project.sprint.get_jira_data('closed_status')

        jira_entries = JiraEntries()
        for s in stories:
            story = JiraEntry()
            story.id = s.find('key').text
            story.is_nice = s.find('title').text.find(nice_identifier) != -1
            story.status = int(s.find('status').get('id'))
            try:
                story.business_value = float(s.find('./customfields/customfield/[@id="customfield_10064"]/customfieldvalues/customfieldvalue').text)
            except AttributeError:
                print 'Story ' + story.id + ' has no business value defined, 0 taken as default'
            try:
                story.story_points = float(s.find('./customfields/customfield/[@id="customfield_10040"]/customfieldvalues/customfieldvalue').text)
            except AttributeError:
                print 'Story ' + story.id + ' has no story points defined, 0 taken as default'
            if story.is_over():
                try:
                    story.close_date = self.get_story_close_date(story.id, closed_status)
                except AttributeError:
                    print 'Story ' + story.id + ' is discarded as it is closed, but never had the status ' + closed_status
                    continue
            jira_entries.append(story)
        return jira_entries

    def get_story_close_date(self, id, closed_status):
        url = "/activity?maxResults=10&issues=activity+IS+issue%3Atransition&streams=issue-key+IS+"
        url += str(id)
        url += '&os_username=' + str(self.username)
        url += '&os_password=' + str(self.password)

        response = self._request(url)
        response_body = response.read()

        response_xml = ET.fromstring(response_body)
        xmlns = {"atom": "http://www.w3.org/2005/Atom"}
        close_date = response_xml.find("./atom:entry/atom:category/[@term='" + closed_status + "']/../atom:published", namespaces=xmlns).text
        return dateutil.parser.parse(close_date)

class ZebraRemote(Remote):
    def __init__(self, base_url, username, password):
        super(ZebraRemote, self).__init__(base_url)

        self.cookiejar = cookielib.CookieJar()
        self.logged_in = False
        self.username = username
        self.password = password

    def _get_request(self, url, body = None, headers = {}):
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'ScrumNanny Zebra Client';
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

    def get_data(self, project):
        report_url = 'timesheet/report/.json?option_selector='

        users = project.get_sprint().get_zebra_data('users')
        client_id = project.get_sprint().get_zebra_data('client_id')
        activities = project.get_sprint().get_zebra_data('activities')
        start_date = project.get_sprint().get_zebra_data('start_date')
        end_date = project.get_sprint().get_zebra_data('end_date')

        for user in users:
            report_url += '&users[]=' + `user`

        report_url += '&projects[]=' + `client_id`
        report_url += '&activities[]=' + str(activities)
        report_url += '&start=' + str(start_date)
        report_url += '&end=' + str(end_date)

        self._login()

        response = self._request(report_url)
        response_body = response.read()

        response_json = json.loads(response_body)
        entries = response_json['command']['reports']['report']
        print 'Will now parse %d entries found in Zebra' % len(entries)

        return self.parse_entries(entries)

    def parse_entries(self, entries):
        zebra_days = ZebraDays()
        for entry in entries:
            # zebra last entries are totals, and dont have a tid
            if entry['tid'] == '':
                continue
            # get a readable date to use as dict key
            date = dateutil.parser.parse(entry['date']).strftime('%Y-%m-%d')

            # parse zebra entry
            zebra_entry = self.parse_entry(entry)

            # add/update zebra entries
            if date in zebra_days:
                zebra_days[date].entries.append(zebra_entry)
                zebra_days[date].time += zebra_entry.time
            else:
                day = ZebraDay()
                day.time = zebra_entry.time
                day.day = date
                day.entries.append(zebra_entry)
                zebra_days[date] = day
        return zebra_days

    def parse_entry(self, entry):
        zebra_entry = ZebraEntry()
        zebra_entry.username = str(entry['username'])
        zebra_entry.time = float(entry['time'])
        return zebra_entry


