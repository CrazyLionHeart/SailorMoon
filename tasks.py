#!/usr/bin/env python
# -*- coding: utf-8 -*-

try:
    from SailorMoon.config import config
    from SailorMoon.rate_limit import RateLimiter

    from lxml import etree
    from base64 import b64decode

    from random import choice
    import smtplib
    import socket
    from sys import stderr
    import logging
    from os import path
    import math

    import json

    import time

    import requests
    from io import BytesIO

    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from email.header import Header
    from email.utils import formataddr, make_msgid, formatdate
    from email.encoders import encode_base64
    from email.parser import Parser

    import dkim

    from celery import Celery, Task
    from celery.utils.log import get_task_logger

    from celery.signals import after_task_publish
    from celery.signals import before_task_publish, task_prerun
    from celery.signals import task_postrun, task_retry, task_success
    from celery.signals import task_failure, task_revoked


except ImportError as e:
    raise e

app = Celery()
app.conf.update(**config['celery'])
logger = get_task_logger(__name__)

url = 'http://%s/ajax/submitajax.php' % config["domain"]
user = 'system'
password = 'system_1234'

auth = requests.auth.HTTPBasicAuth(user, password)

rate = RateLimiter(redis_host=config['Redis']['host'])
rate.add_condition({'requests': 1, 'minutes': 1})


class ConnectionError(Exception):
    pass


class AuthError(Exception):
    pass


class SendError(Exception):
    pass


class ServerRefuse(Exception):
    pass


class NoMX(Exception):
    pass


class MySMTP(smtplib.SMTP):

    def __init__(self, host='', port=0, local_hostname=None,
                 timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 source_address=None):
        """Initialize a new instance.

        If specified, `host' is the name of the remote host to which to
        connect.  If specified, `port' specifies the port to which to connect.
        By default, smtplib.SMTP_PORT is used.  If a host is specified the
        connect method is called, and if it returns anything other than a
        success code an SMTPConnectError is raised.  If specified,
        `local_hostname` is used as the FQDN of the local host in the HELO/EHLO
        command.  Otherwise, the local hostname is found using
        socket.getfqdn(). The `source_address` parameter takes a 2-tuple (host,
        port) for the socket to bind to as its source address before
        connecting. If the host is '' and port is 0, the OS default behavior
        will be used.

        """
        self._host = host
        self.timeout = timeout
        self.esmtp_features = {}
        self.source_address = source_address

        if host:
            (code, msg) = self.connect(host, port)
            if code != 220:
                raise smtplib.SMTPConnectError(code, msg)
        if local_hostname is not None:
            self.local_hostname = local_hostname
        else:
            # RFC 2821 says we should use the fqdn in the EHLO/HELO verb, and
            # if that can't be calculated, that we should use a domain literal
            # instead (essentially an encoded IP address like [A.B.C.D]).
            fqdn = socket.getfqdn()
            if '.' in fqdn:
                self.local_hostname = fqdn
            else:
                # We can't find an fqdn hostname, so use a domain literal
                addr = '127.0.0.1'
                try:
                    addr = socket.gethostbyname(socket.gethostname())
                except socket.gaierror:
                    pass
                self.local_hostname = '[%s]' % addr

    def _get_socket(self, host, port, timeout):
        # This makes it simpler for SMTP_SSL to use the SMTP connect code
        # and just alter the socket connection bit.
        if self.debuglevel > 0:
            print>>stderr, 'connect: to', (host, port), self.source_address
        return socket.create_connection((host, port), timeout,
                                        self.source_address)

    def connect(self, host='localhost', port=0, source_address=None):
        """Connect to a host on a given port.

        If the hostname ends with a colon (`:') followed by a number, and
        there is no port specified, that suffix will be stripped off and the
        number interpreted as the port number to use.

        Note: This method is automatically invoked by __init__, if a host is
        specified during instantiation.

        """

        if source_address:
            self.source_address = source_address

        if not port and (host.find(':') == host.rfind(':')):
            i = host.rfind(':')
            if i >= 0:
                host, port = host[:i], host[i + 1:]
                try:
                    port = int(port)
                except ValueError:
                    raise OSError("nonnumeric port")
        if not port:
            port = self.default_port
        if self.debuglevel > 0:
            print>>stderr, 'connect:', (host, port)
        self.sock = self._get_socket(host, port, self.timeout)
        self.file = None
        (code, msg) = self.getreply()
        if self.debuglevel > 0:
            print>>stderr, "connect:", msg
        return (code, msg)


# @setup_logging.connect
# def configure_logging(loglevel, logfile, format, colorize, *args, **kwargs):
#     from logging.config import dictConfig
#     dictConfig(config["loggingconfig"])
#     self.log.info('logging configured')
#     logger.info('logging configured')


@before_task_publish.connect
def before_task_publish_handler(body, exchange, routing_key, headers,
                                properties, declare, retry_policy, *args,
                                **kwargs):
    logger.info("before_task_publish")


@after_task_publish.connect
def after_task_publish_handler(body, exchange, routing_key, *args, **kwargs):
    logger.info("after_task_publish")


@task_prerun.connect
def task_prerun_handler(task_id, task, *arg, **kwargs):
    logger.info("task_prerun")


@task_postrun.connect
def task_postrun_handler(task_id, task, args, kwargs, retval, state, signal,
                         sender):
    logger.info("task_postrun")


@task_retry.connect
def task_retry_handler(request, reason, einfo, *args, **kwargs):
    logger.info("task_retry")


@task_success.connect
def task_success_handler(result, *args, **kwargs):
    logger.info("task_success")


@task_failure.connect
def task_failure_handler(task_id, exception, args, kwargs, traceback, einfo,
                         signal, sender):
    logger.info("task_failure")


@task_revoked.connect
def task_revoked_handler(request, terminated, signum, expired, *args,
                         **kwargs):
    logger.info("task_revoked")


class _BaseTask(Task):

    abstract = True

    @classmethod
    def task(cls):
        return cls.app.tasks[cls.name]

    @property
    def type(self):
        pass

    @property
    def log(self):
        return get_task_logger('%s.%s' % (__name__, self.__class__.__name__))

    def __init__(self, *args, **kwargs):
        return super(_BaseTask, self).__init__(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        """In celery task this function call the run method, here you can
        set some environment variable before the run of the task"""
        self.log.info("Task started")
        return super(_BaseTask, self).__call__(*args, **kwargs)

    def on_success(self, *args, **kwargs):
        self.log.info("on_success callback")

    def on_retry(self, *args, **kwargs):
        self.log.info("on_retry callback")

    def on_failure(self, *args, **kwargs):
        self.log.info("on_failure: Task failed")

    def after_return(self, *args, **kwargs):
        # exit point of the task whatever is the state
        self.log.info("after_return: Task finished")
        super(_BaseTask, self).after_return(*args, **kwargs)


class Mailer(_BaseTask):

    store_errors_even_if_ignored = True

    def get_mx(self, host):

        from dns import resolver

        try:
            MX = choice(resolver.query(host, 'MX'))
            return MX.exchange.to_text()[:-1]
        except Exception as exc:
            raise NoMX(
                "Cannot find MX-server: %s, %r" %
                (host, exc))

    class StderrLogger(object):

        def __init__(self):
            self.logger = logging.getLogger(
                '%s.%s' % (__name__, self.__class__.__name__))

        def write(self, message):
            self.logger.debug(message)

    def __init__(self):
        self.log.info("Init messaging class")

    def run(self, *args, **kwargs):

        self.log.info("Task begin process")

        host = None

        try:
            new_args = args[0]
            body, to_email, from_email = new_args

            splitFrom = from_email.split('@')
            from_host = splitFrom[1]

            host = to_email.split('@')[1]

            mx_domain = self.get_mx(host)

            smtplib.stderr = self.StderrLogger()

            try:
                source_address = (socket.gethostbyname(from_host), 0)

                mailserver = MySMTP(mx_domain, local_hostname=from_host,
                                    source_address=source_address)
            except Exception:

                from_host = 'babypages.ru'

                try:
                    mailserver = MySMTP(mx_domain)
                except Exception as detail:
                    self.log.exception(detail)
                    raise ConnectionError(
                        "Error connecting to %s: %r" % (mx_domain, detail))

            mailserver.set_debuglevel(1)

            # identify ourselves to smtp gmail client
            if mailserver.has_extn('STARTTLS'):
                # secure our email with tls encryption
                mailserver.starttls()

                # re-identify ourselves as an encrypted connection
                mailserver.ehlo(from_host)
            else:
                mailserver.helo(from_host)

            refused = {}

            try:
                mailserver.sendmail(from_email, to_email, body)
            except smtplib.SMTPRecipientsRefused as detail:
                self.log.error(detail, exc_info=True)

                errcode, errmsg = detail[0][to_email]

                if errcode >= 400 and errcode < 500:
                    raise SendError("Couldn't send message: %s" % errmsg)

                if errcode >= 500:
                    raise ServerRefuse(
                        "Server refuse accept message: %s" % errmsg)

                refused[to_email] = (errcode, errmsg)

            except (socket.error, smtplib.SMTPException) as e:
                self.log.error(e, exc_info=True)
                # All recipients were refused.  If the exception had an
                # associated
                # error code, use it.  Otherwise,fake it with a non-triggering
                # exception code.
                errcode = getattr(e, 'smtp_code', -1)
                errmsg = getattr(e, 'smtp_error', 'ignore')

                if errcode >= 400 and errcode < 500:
                    raise SendError("Couldn't send message: %s" % errmsg)

                if errcode >= 500:
                    raise ServerRefuse(
                        "Server refuse accept message: %s" % errmsg)
                refused[to_email] = (errcode, errmsg)

            mailserver.quit()
        except ServerRefuse as exc:
            self.log.error(exc, exc_info=True)
            raise exc
        except NoMX as exc:
            self.log.error(exc, exc_info=True)
            raise exc
        except Exception as exc:
            self.log.error(exc, exc_info=True)
            if host:
                time_delay = math.ceil(rate.acquire(host, False))
            else:
                time_delay = 180

            self.log.info("Current delay: %s" % time_delay)

            raise self.retry(exc=exc, max_retries=5, countdown=time_delay)

        self.log.info("Task end process")


class CloseDoc(_BaseTask):

    def run(self, *args, **kwargs):
        try:
            self.log.info("doc closing")

            _, guid = args

            self.log.info("Closing document: %s" % guid)

            props = json.dumps({'end_process': int(time.time() * 1000)})

            payload = dict(
                ajtype='jqGridAllSave', datatype='email_message',
                json_row_data=json.dumps(
                    [{'doc_pin': guid,
                      'price_closed': 't',
                      'props': props}]))

            r = requests.post(url, auth=auth, data=payload)
            r.raise_for_status()
            self.log.info("Doc closed with response: %s" %
                          r.text)
            return r.json()
        except Exception as exc:
            raise self.retry(exc=exc, max_retries=5)


class OpenDoc(_BaseTask):

    def run(self, *args, **kwargs):
        try:
            _, guid = args

            self.log.info("Opening document: %s" % guid)

            props = json.dumps({'end_process': int(time.time() * 1000)})

            payload = dict(
                ajtype='jqGridAllSave', datatype='email_message',
                json_row_data=json.dumps(
                    [{'doc_pin': guid,
                      'kolvo_closed': 'f',
                      'props': props}]))

            r = requests.post(url, auth=auth, data=payload)
            r.raise_for_status()
            self.log.info("Opening doc with status: %s" %
                          r.text)
            return r.json()
        except Exception as exc:
            raise self.retry(exc=exc, max_retries=5)


class GetXML(_BaseTask):

    def run(self, guid, *args, **kwargs):
        try:
            props = json.dumps({'begin_process': int(time.time() * 1000)})

            payload = dict(
                ajtype='jqGridAllSave', datatype='email_message',
                json_row_data=json.dumps(
                    [{'doc_pin': guid,
                      'props': props}]))

            r = requests.post(url, auth=auth, data=payload)
            r.raise_for_status()
            self.log.info("Save props with status: %s" %
                          r.json())

            self.log.info("Fetching document")

            self.log.info("Fetching document: %s" % guid)

            r = requests.get('http://%s/docxml/doc/%s/1/false' %
                             (config['domain'], guid), stream=True)
            r.raise_for_status()
            data = BytesIO(r.content)

            self.log.info("End fetching")

            self.log.info("Start parsing")
            p = etree.XMLParser(huge_tree=True)
            self.log.info("End parsing")

            root = etree.parse(data, p)

            email = {
                "headers": {
                    "from": {
                        "name": root.find("agent_name").text,
                        "email": root.find("agent_email").text
                    },
                    "Subject": root.find("name").text,
                    "guid": guid
                },
                "body": {
                    "plain": root.find("plain_message").text or "",
                    "html": root.find("data").text,
                    "attachment": []
                }
            }

            if root.find("client_email").text is None:
                raise Exception("No email")

            if root.find('detail') is not None:

                for document in root.find('detail'):
                    if document.find('file_hash') is not None:
                        file_hash = document.find('file_hash')
                        content_type = file_hash.attrib['content_type']
                        to_attach = {
                            "mimetype": content_type,
                            "content": b64decode(file_hash.text),
                            "filename": document.find('name').text,
                            "guid": document.find("doc_pin").text
                        }

                        email['body']['attachment'].append(to_attach)

            email['headers']['to'] = {
                "name": root.find("contragent_name").text,
                "email": root.find("client_email").text
            }

            return email
        except Exception as exc:
            raise self.retry(exc=exc, max_retries=5)


class MakeEmail(_BaseTask):

    valid_include_headers = ('From', 'To', 'Sender', 'Reply-To', 'Subject',
                             'Date', 'Message-ID', 'Cc', 'MIME-Version',
                             'Content-Type', 'Content-Transfer-Encoding',
                             'Content-ID', 'Content-Description',
                             'Resent-Date',
                             'Resent-From', 'Resent-Sender', 'Resent-To',
                             'Resent-Cc', 'Resent-Message-ID', 'In-Reply-To',
                             'References', 'List-Id', 'List-Help',
                             'List-Unsubscribe', 'List-Subscribe', 'List-Post',
                             'List-Owner', 'List-Archive')

    def run(self, email, *args, **kwargs):
        self.log.info("Making email letter")

        headers = email['headers']
        body = email['body']

        splitFrom = headers['from']['email'].split('@')

        from_host = splitFrom[1]
        replyTo = '%s+%s@%s' % (splitFrom[0], headers['guid'], from_host)

        unsubscribe = '%s+unsubscribe_%s@%s' % (
            splitFrom[0], headers['guid'], from_host)

        self.log.info("Begin real sending to %s" % headers['to']['email'])

        mixed = MIMEMultipart('mixed')
        related = MIMEMultipart('related')

        html_part = MIMEText(body['html'], 'html', 'utf-8')
        text_part = MIMEText(body['plain'], 'plain', 'utf-8')

        # Create message container - the correct MIME type is
        # multipart/alternative.
        alternative = MIMEMultipart(
            'alternative', None, [text_part, html_part])

        mixed['From'] = formataddr((str(Header(
                                        headers['from']['name'],
                                        'utf-8')),
                                    headers['from']['email']))

        mixed['To'] = formataddr((str(Header(
            headers['to']['name'],
            'utf-8')),
            headers['to']['email']))

        mixed['Subject'] = headers['Subject']

        mixed['Reply-To'] = replyTo

        Message_Id = make_msgid(headers['guid'])

        mixed['Message-ID'] = Message_Id
        mixed['Date'] = formatdate(localtime=True)
        mixed['Precedence'] = 'bulk'
        mixed['Sensitivity'] = 'Personal'
        mixed['Content-Language'] = 'ru'

        mixed['List-Unsubscribe'] = '<mailto:%s>' % unsubscribe

        mixed["X-Sender"] = formataddr((str(Header(
                                        headers['from']['name'],
                                        'utf-8')),
                                        headers['from']['email']))

        mixed['List-id'] = headers['guid']
        mixed["X-Mailer"] = "smtplib"
        mixed["X-Priority"] = "3"  # 1 UrgentMessage, 3 Normal

        mixed["Importance"] = "3"

        for element in body['attachment']:

            filename = element['filename']

            try:
                if element['mimetype']:
                    maintype, subtype = element['mimetype'].split('/', 1)
                else:
                    maintype, subtype = ('application', 'octet-stream')
            except ValueError:
                maintype, subtype = ('application', 'octet-stream')

            part = MIMEBase(maintype, subtype)
            part.set_payload(element['content'])

            # Encode the payload using Base64
            encode_base64(part)

            # Set the filename parameter
            part.add_header('Content-Disposition', 'attachment',
                            filename="%s" % Header(filename, 'utf-8'))
            mixed.attach(part)

        # If message have inline image - it must be attached
        # to related section
        related.attach(alternative)
        mixed.attach(related)

        config_path = path.dirname(path.abspath(__file__))

        dkim_exist = False

        try:
            key_path = '%s/config/dkim.%s._.key' % (config_path, from_host)
            f = open(key_path)
            f.close()
            dkim_exist = True
        except IOError:
            try:
                from_host = 'babypages.ru'
                mixed["Envelope-From"] = 'robot@%s' % from_host
                key_path = '%s/config/dkim.%s._.key' % (config_path, from_host)
                f = open(key_path)
                f.close()
                dkim_exist = True
            except IOError:
                pass

        if dkim_exist:
            with open(key_path) as f:

                selector = '_'

                privkey = f.read()

                include_headers = frozenset(
                    self.valid_include_headers).intersection(mixed.items())

                try:
                    dkim_header = dkim.sign(
                        mixed.as_string(), selector, from_host, privkey,
                        canonicalize=(dkim.Relaxed, dkim.Relaxed),
                        include_headers=include_headers)

                    dkim_header = Parser().parsestr(dkim_header)
                    dkim_value = dkim_header['DKIM-Signature']

                    mixed['dkim-signature'] = dkim_value
                except dkim.KeyFormatError as e:
                    self.log.exception(e)

        self.log.info("Message lenght: %d" % len(mixed.as_string()),
                      exc_info=True)

        result = (mixed.as_string(), headers['to']['email'],
                  headers['from']['email'])

        return result


class FillError(_BaseTask):

    def run(self, *args, **kwargs):
        try:
            self.log.info("Filling error info")

            task_id, guid = args

            result = app.AsyncResult(task_id)
            if result.ready:
                log = '--\n\n{0} {1} {2}'.format(task_id, result.result,
                                                 result.traceback)
                self.log.error(log, exc_info=True)

                payload = dict(
                    ajtype='extended_props', datatype='save',
                    docguid=guid, doctype='email_message',
                    props=json.dumps({
                                     'last_error_message': log
                                     })
                )

                r = requests.post(url, auth=auth, data=payload)
                r.raise_for_status()
                self.log.info(
                    u"Сохранение текста ошибки прошло со статусом: %s" %
                    r.text)
                return r.json()

            else:
                self.log.error("""Попытка получить результат функции
                               которая еще не выполнена""", exc_info=True)
        except Exception as exc:
            raise self.retry(exc=exc, max_retries=5)


if __name__ == '__main__':
    app.worker_main()
