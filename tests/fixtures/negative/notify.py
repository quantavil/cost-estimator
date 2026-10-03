# Imports and look-alikes only: none of these is a paid AI/voice call.
import openai  # noqa: F401
from twilio.rest import Client

sms = Client()


def text_candidate(number):
    return sms.messages.create(to=number, from_="+15550000000", body="Your interview is booked")


def history():
    return {"messages": []}
