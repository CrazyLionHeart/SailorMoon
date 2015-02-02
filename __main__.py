#!/bin/env python
# -*- coding: utf-8 -*-

from SailorMoon.mailer import SailorMoon
from SailorMoon.config import config
from SailorMoon.TokenBucket import TokenBucket

import logging
from SailorMoon.test_data import email_1, email_2, email_3, email_4


delay = config['delay']

for domain in delay:
    rate = delay[domain]['limit']
    bucket_name = delay[domain]['bucket']
    logging.info("Create bucket '%s'. Set delay: %f" % (bucket_name, rate))
    globals()[bucket_name] = TokenBucket()
    globals()[bucket_name].set_rate(1.0 / rate)

defaultBucket = TokenBucket()
defaultBucket.set_rate(0)


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

try:
    for item in (email_1,):

        headers = item['headers']
        body = item['body']

        domain = headers['to']['email'].split('@')[1]

        delay = config['delay']

        domain = headers['to']['email'].split('@')[1]
        if domain in delay:
            bucket_name = delay[domain]['bucket']
        else:
            bucket_name = 'defaultBucket'

        current_bucket = globals()[bucket_name]

        mailer = SailorMoon()

        logging.info('Current bucket name: %s' % bucket_name)

        logs = mailer.send(headers, body)
except KeyboardInterrupt, e:
    exit(1)
