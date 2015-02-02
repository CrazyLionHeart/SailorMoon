#!/bin/env python
# -*- coding: utf-8 -*-

try:
    from SailorMoon.tasks import GetXML, MakeEmail, FillError, Mailer, OpenDoc, CloseDoc
    from SailorMoon.config import config

    from celery import signature

    import logging
    import logging.config

    import requests

    from stompest.sync import Stomp
    from stompest.config import StompConfig
    from stompest.protocol import StompSpec

    from raven import Client

except ImportError, e:
    raise e


activemq = config['activemq']
default_uri = '''failover:(tcp://%(host)s:%(port)d,tcp://%(host)s:%(port)d)?randomize=%(randomize)s,startupMaxReconnectAttempts=%(startupMaxReconnectAttempts)d,initialReconnectDelay=%(initialReconnectDelay)d,maxReconnectDelay=%(maxReconnectDelay)d,maxReconnectAttempts=%(maxReconnectAttempts)d''' % activemq[
    'stomp']

url = 'http://%s/ajax/submitajax.php' % config["domain"]
user = 'system'
password = 'system_1234'

auth = requests.auth.HTTPBasicAuth(user, password)

queue = "/queue/%s.%s" % (
    config['queue']['email_system'], config["APPLICATION_ENV"])

dsn = "http://%s:%s@%s" % (config['Raven']['public'],
                           config['Raven']['private'],
                           config['Raven']['host'])

sentry = Client(dsn)


class ActiveMQ(object):
    ERROR_QUEUE = '/queue/SailorMoonError'

    def __init__(self, config=None):
        if config is None:
            config = StompConfig(default_uri)
        self.config = config

    def run(self):
        client = Stomp(self.config)
        client.connect()
        headers = {
            # client-individual mode is necessary for concurrent processing
            # (requires ActiveMQ >= 5.2)
            StompSpec.ACK_HEADER: StompSpec.ACK_CLIENT_INDIVIDUAL,
            # the maximal number of messages the broker will let you work on at
            # the same time
            'activemq.prefetchSize': '1',
        }
        client.subscribe(queue, headers)
        while True:
            frame = client.receiveFrame()
            doc_pin = frame.body

            try:
                fillerror = FillError().s(doc_pin)
                opendoc = OpenDoc().s(doc_pin)
                closedoc = CloseDoc().s(doc_pin)

                catchError = (fillerror | opendoc)

                getxml = signature('SailorMoon.tasks.GetXML',
                                   args=(doc_pin,), debug=True,
                                   link_error=catchError)
                makeemail = signature('SailorMoon.tasks.MakeEmail',
                                      debug=True,
                                      link_error=catchError)
                schedule_email = signature('SailorMoon.tasks.Mailer',
                                           debug=True,
                                           link_error=catchError)

                res = (getxml | makeemail | schedule_email | closedoc)
                res()
            except Exception, e:
                logging.exception(e)
            client.ack(frame)


if __name__ == '__main__':

    logging.basicConfig(level=logging.INFO)
    try:
        ActiveMQ().run()
    except KeyboardInterrupt:
        exit(0)
