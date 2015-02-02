#!/usr/bin/env python
# -*- coding: utf-8 -*-

try:
    from os import environ, path
    import json
    from logging.config import dictConfig
    from raven import Client
    from io import open
except ImportError, e:
    raise e

current_env = environ.get("APPLICATION_ENV", 'development')

try:
    with open(
        '%s/%s/config.%s.json' % (path.dirname(path.abspath(__file__)),
                                  current_env, current_env),
            encoding='utf-8') as f:
        config = json.load(f)
        config["APPLICATION_ENV"] = current_env

        dictConfig(config["loggingconfig"])
        dsn = "http://%s:%s@%s" % (config['Raven']['public'],
                                   config['Raven']['private'],
                                   config['Raven']['host'])
        client = Client(dsn)

except IOError, e:
    print(
        "Конфиг не найден. Поместите в файл: %s/%s/config.%s.json" % (path.dirname(path.abspath(__file__)),
                                                                      current_env, current_env))
    exit(1)
