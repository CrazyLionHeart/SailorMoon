#!/usr/bin/env python
# -*- coding: utf-8 -*-

from SailorMoon.tasks import GetXML, MakeEmail, FillError, Mailer, OpenDoc, CloseDoc
from celery import signature

doc_pin = 'a1af6306-73c3-11e4-b8d4-525400283fee'
#doc_pin = 'foo'

fillerror = FillError().s(doc_pin)
opendoc = OpenDoc().s(doc_pin)
closedoc = CloseDoc().s(doc_pin)

catchError = (fillerror | opendoc)

getxml = signature('SailorMoon.tasks.GetXML',
                   args=(doc_pin,), debug=True, link_error=catchError)
makeemail = signature('SailorMoon.tasks.MakeEmail', debug=True,
                      link_error=catchError)
schedule_email = signature('SailorMoon.tasks.Mailer', debug=True,
                           link_error=catchError)

res = (getxml | makeemail | schedule_email | closedoc)()
print(res.get())
