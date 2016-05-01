import copy
import json
import logging
import yaml
import requests
import sys, getopt
from urlparse import urlparse, urlunparse

INSIGHTS_QUERY_KEY = 'YGXxtYyF0xa5e6apPw-nbWioqJbgxmkp'
INSIGHTS_ACCOUNT_ID = 213456

logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.DEBUG)


def log_call(fn):
    from itertools import chain

    def wrapped(*v, **k):
        name = fn.__name__
        ret = fn(*v, **k)
        log.info("%s(%s)\n%s" % (name, ", ".join(map(repr, chain(v, k.values()))), ret))
        return ret

    return wrapped


class Insights:
    def __init__(self, filters):
        self.filters = filters
        self.since = '1 week ago'

    def get_filter_string(self):
        return ' and '.join(self.filters)
        # return ''.join([(" %s = '%s'" % (key, val)) for key, val in self.filters.iteritems()])

    @log_call
    def execute_query(self, query):
        # curl -H "Accept: application/json" -H "X-Query-Key: YOUR_KEY_HERE" "https://insights-api.newrelic.com/v1/accounts/213456/query?nrql=SELECT%20average%28duration%29%20FROM%20PageView"
        headers = {
            'Accept': 'application/json',
            'X-Query-Key': INSIGHTS_QUERY_KEY
        }
        url = "https://insights-api.newrelic.com/v1/accounts/%d/query" % INSIGHTS_ACCOUNT_ID
        result = requests.get(url, headers=headers, params={'nrql': query})
        results = result.json()

        if 'error' in results:
            log.error(results['error'])
            return None

        for field_name in ['facets']:
            if field_name in results:
                return results[field_name]
        return results['results'][0]['events']

    def sessions(self, urls, limit=100):
        urls_string = '\',\''.join(urls)
        query = "SELECT session FROM PageView SINCE %s WHERE pageUrl in ('%s') LIMIT %d" % (
            self.since,
            urls_string,
            limit)
        return [x['session'] for x in self.execute_query(query)]

    def scenarios(self, sessions):
        sessions_string = '\',\''.join(list(sessions))
        query = "SELECT pageUrl,backendDuration,session,user FROM PageView SINCE %s WHERE %s and session in ('%s') LIMIT 1000" % (
            self.since,
            self.get_filter_string(), sessions_string)
        pages = self.execute_query(query)
        scenarios = {}
        for page in pages:
            if page['session'] not in scenarios:
                scenarios[page['session']] = []
            scenarios[page['session']].append({
                'url': page['pageUrl'],
                'time': page['timestamp'],
                'duration': 1000 * page['backendDuration']
            })
        return scenarios

    def slowest_pages(self):
        query = "SELECT average(backendDuration) FROM PageView SINCE %s FACET pageUrl WHERE %s" % (
            self.since, self.get_filter_string())
        return self.execute_query(query)

    def sessions_with_most_pages(self, sessions, limit=3):
        sessions_string = '\',\''.join(list(sessions))
        query = "SELECT uniqueCount(pageUrl) FROM PageView FACET session SINCE %s WHERE %s and session in ('%s') LIMIT %d" % (
            self.since,
            self.get_filter_string(),
            sessions_string,
            limit
        )
        result = self.execute_query(query)
        return [x['name'] for x in result]


class Taurus:
    def __init__(self, duration_delta=10000, hosts_override=None, default_scenario=None, required_load=10):
        self.duration_delta = duration_delta
        self.hosts_override = hosts_override
        if default_scenario is not None:
            self.default_scenario = default_scenario
        else:
            self.default_scenario = {
                "concurrency": { 
                    "local" : int(required_load) ,
                    "cloud" : int(required_load) 
                },
                "locations": { 
                    "us-east1-b" : 1 ,
                    "us-west-2" : 1
                } ,
                "locations-weighted" : "true",
                "hold-for": "2m", "ramp-up": "20s",
                "scenario": {
                    "requests": [],
                    "retrieve-resources": True
                }
            }

    pass

    def build_script(self, scenarios):
        return {
            "execution": self.get_scenarios(scenarios),
            "provisioning": "local",
            "reporting": [
                {
                    "module": "final_stats"
                },
                {
                    "module": "console"
                }
            ],
            "settings": {
                "check-interval": "5s",
                "default-executor": "jmeter"
            }
        }

    def get_scenarios(self, scenarios):
        ret = []
        for scenario in scenarios.itervalues():
            new_scenario = copy.deepcopy(self.default_scenario)
            new_scenario['scenario']['requests'] = self.get_requests(scenario)
            ret.append(new_scenario)
        return ret

    def get_requests(self, pages):
        ret = []
        for page in pages:
            ret.append({
                'url': self.url_parse(page['url']),
                'method': 'GET',
                'timeout': self.duration_delta + page['duration']
            })
        return ret

    def write_yaml(self, path, script):
        with open(path, 'w') as outfile:
            outfile.write(yaml.safe_dump(script, default_flow_style=False, encoding='utf-8', allow_unicode=True))

    def write_json(self, path, script):
        with open(path, 'w') as outfile:
            outfile.write(json.dumps(script, indent=4))

    def url_parse(self, url):
        if self.hosts_override:
            parts = urlparse(url)
            if parts.netloc in self.hosts_override:
                return urlunparse(parts._replace(netloc=self.hosts_override[parts.netloc]))
        return url


def main(argv):

    load = 10
    outputfile = 'prod_test.json'
    is_yml = 0
    is_prod = 0

    try:
        opts, args = getopt.getopt(argv,"hl:o:y:p")
    except getopt.GetoptError:
        print sys.argv[0] + ' -l <required load> -o <outputfile withough suffix> -y -p'
        sys.exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print sys.argv[0] + ' -l <required load> -o <outputfile withough suffix> -y -p'
            sys.exit()
        elif opt in ("-l"):
            load = arg
        elif opt in ("-o"):
            outputfile = arg
        elif opt in ("-y"):
            is_yml = 1
        elif opt in ("-p"):
            is_prod = 1
    print 'Load is "', load
    if is_yml == 1:
        print 'Format YML'
        outputfile = outputfile + '.yml'
    else:
        print 'Format JSON'
        outputfile = outputfile + '.json'
    print 'Output file is "', outputfile

    # sys.exit()

    insights = Insights(
        filters=['appName=\'BZA Commercial\'',
                 'session != \'fffa5e2ec8eb4575\''],
    )
    pages = insights.slowest_pages()
    urls = [page['name'] for page in pages]
    all_sessions = insights.sessions(urls)
    sessions = insights.sessions_with_most_pages(all_sessions)
    scenarios = insights.scenarios(sessions)
    if is_prod == 0:
        taurus = Taurus(
            hosts_override={
                'www.blazemeter.com': 'wwwdev.blazemeter.com',
                'blazemeter.com': 'wwwdev.blazemeter.com',
            },
            duration_delta=1000,
            required_load=load
        )
    else:
        taurus = Taurus(
            hosts_override={
                'www.blazemeter.com': 'www.blazemeter.com',
                'blazemeter.com': 'www.blazemeter.com',
            },
            duration_delta=1000,
            required_load=load
        )
    script = taurus.build_script(scenarios)
    if is_yml == 1:
        taurus.write_yaml(outputfile, script)
    else:
        taurus.write_json(outputfile, script)


if __name__ == '__main__':
    main(sys.argv[1:])