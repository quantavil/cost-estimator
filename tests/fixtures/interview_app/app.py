from openai import OpenAI
from deepgram import DeepgramClient
from elevenlabs.client import ElevenLabs
import requests

client = OpenAI()
dg = DeepgramClient()
tts = ElevenLabs()


def answer(history):
    return client.chat.completions.create(model="gpt-4o-mini", messages=history)


def transcribe(audio):
    return dg.listen.rest.v("1").transcribe_file({"buffer": audio}, {"model": "nova-3"})


def speak(text):
    return tts.text_to_speech.convert(text=text, voice_id="abc", model_id="eleven_flash_v2_5")


def notify(url):
    return requests.post(url, json={"done": True})
