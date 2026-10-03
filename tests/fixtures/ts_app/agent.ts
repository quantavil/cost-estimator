import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";

const anthropic = new Anthropic();
const openai = new OpenAI();

export async function reply(messages: any[]) {
  return anthropic.messages.create({ model: "claude-sonnet-4-5", max_tokens: 400, messages });
}

export async function speak(input: string) {
  return openai.audio.speech.create({ model: "tts-1", voice: "alloy", input });
}

export async function webhook(url: string) {
  return fetch(url, { method: "POST" });
}
