# AVARA setup guide

## What AVARA needs

Install 64-bit Python 3.11 or newer and choose at least one AI option: a Groq API key, a Gemini API key, or a local model running in Ollama. Internet is needed for first-time setup and activation. Provider usage charges are separate from the AVARA plan.

## Install

1. Sign in to the AVARA storefront using the account used for your cash order.
2. After approval, open **Your orders** and choose **Download AVARA**.
3. Install the latest stable 64-bit Python from <https://www.python.org/downloads/windows/>. Enable “Add Python to PATH” if offered. If a dependency does not support the latest version, try Python 3.11 or 3.12.
4. Extract the entire ZIP into a folder you control and read START_HERE.txt.
5. Double-click START_AVARA_WINDOWS.bat and leave its window open while dependencies install.
6. Send the one-time code displayed in AVARA and your order number to the owner. Keep AVARA open while it checks for approval.

## Configure AI

- Groq: create a key at <https://console.groq.com/keys>.
- Gemini: create a key at <https://aistudio.google.com/app/apikey>.
- Ollama: install <https://ollama.com/download>, download a compatible model from <https://ollama.com/library>, and keep Ollama running.
- In AVARA's welcome setup or Settings → AI & voice, select your provider, enter a key if using Groq or Gemini, and save and test the connection.
- Google and other integrations require their own authorization.

Never email passwords or API keys, paste them into the storefront, or include them in screenshots.

## Verify

Confirm that AVARA starts, the selected AI connection reports online, and a simple chat works. Then configure optional features such as Phone Remote, Google integrations, or voice.

If the approved download is missing, refresh your order status and sign in again. Contact AVARA support with the order number only—never with your password or API keys.
