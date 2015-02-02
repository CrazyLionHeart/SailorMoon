#!/bin/env python
# -*- coding: utf-8 -*-

from os import environ, path
import json
import logging

current_env = environ.get("APPLICATION_ENV", 'development')

try:
    with open(
        '%s/config/%s/config.%s.json' % (path.dirname(path.abspath(__file__)),
                                         current_env, current_env)) as f:
        config = json.load(f)
        logging.config.dictConfig(config['loggingconfig'])


except IOError, e:
    with open(
        '%s/config/%s/sample_config.json' % (path.dirname(path.abspath(__file__)),
                                             current_env)) as f:
        print(
            "Конфиг не найден. Поместите в файл: %s/config/%s/config.%s.json" % (path.dirname(path.abspath(__file__)),
                                                                                 current_env, current_env))
        print("Пример конфига: %s " % f.read())
    exit(1)
