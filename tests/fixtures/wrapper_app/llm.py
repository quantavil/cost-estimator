from openai import OpenAI

client = OpenAI()


def ask(prompt):
    # One billable call site, reached from three places below.
    return client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}])


def greet(name):
    return ask(f"Greet {name}")


def follow_up(answer):
    return ask(f"Follow up on: {answer}")


def score(transcript):
    return ask(f"Score: {transcript}")
