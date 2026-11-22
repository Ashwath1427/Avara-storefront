# AVARA setup guide

## Download and install

1. Download AVARA from the website's Download section. There is one installer per system, the same for every plan. No account or order is needed to download.
2. Windows: run `AVARA-…-windows-setup.exe`. Mac (Apple Silicon, macOS 14 or later): open the DMG and drag AVARA to Applications.
3. Open AVARA and choose your plan. AVARA shows a one-time code such as `AVR-7F3K-92QD`.
4. Send the code to the AVARA owner on WhatsApp and pay by cash or UPI. Keep AVARA open and online; it unlocks itself after approval and shows your plan and expiry.
5. The code works once, only on this computer, and expires after 48 hours unapproved. Changing the plan before approval gives a new code.

## Choose one AI option

- **Groq:** create a key at [Groq API Keys](https://console.groq.com/keys).
- **Gemini:** create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).
- **Local AI:** install [Ollama](https://ollama.com/download), download a compatible model from its [model library](https://ollama.com/library), and keep Ollama running.

Use the welcome setup or **Settings → AI & voice** to choose your provider, save its key or model, and test a simple chat. You need only one AI option. Groq/Gemini require internet and have their own limits or charges. Local models need sufficient computer resources. Optional voice, image, video, Google, and other integrations have separate setup requirements.

Enter API keys only in AVARA's local setup/settings. Never send passwords, activation tokens, or API keys in public messages or screenshots.

## Approval and subscriptions

Monthly Basic is ₹150 and monthly Pro is ₹200. Six-month Basic is ₹1,000 and six-month Pro is ₹1,400. Plans last 30 or 180 days from approval; renewing early adds to the current expiry.

AVARA keeps a signed copy of its last plan check and works offline for up to 7 days (never past the plan's expiry), then needs to re-check online. The owner can pause, continue, renew, cancel, change or extend a plan. Existing customers who install the new version on the same computer and user keep their plan automatically.

## Source ZIPs only

Older source ZIPs require 64-bit Python 3.11 or newer. Use the [Windows](https://www.python.org/downloads/windows/) or [macOS](https://www.python.org/downloads/macos/) Python download page. Use a Python version supported by the release dependencies; the newest version is not automatically compatible with every package. On Windows enable Add Python to PATH. Extract the entire ZIP and follow START_HERE.txt or the supplied START_AVARA_WINDOWS.bat / START_AVARA_MAC.command launcher. These steps do not apply to packaged EXE/DMG downloads.

## Mac permissions and testing

Screen understanding, microphone, camera, and app control may need the corresponding macOS permissions. Review each request yourself. Windows-specific automation is not guaranteed on macOS. Mac availability in the storefront does not mean every feature has been tested on both Mac architectures.

## Troubleshooting and uninstall

- Download not available: the release may still be uploading. Try again later or contact the owner.
- Mac compatibility: the current release requires Apple Silicon and macOS 14 or later.
- Plan check cannot connect: confirm internet access and wait a minute for the service to wake. If it persists, send the owner your code and a redacted log.
- AI offline: test your chosen provider, key, quota, or running Ollama model.
- Startup failure: read AVARA's log under Local AppData/AVARA/logs on Windows or Library/Application Support/AVARA/logs on Mac. Share only a redacted error excerpt.
- Unrecognized publisher warnings: download AVARA only from the official website. Stop if the source is uncertain; do not disable system protection.
- Windows packaged install: use Settings → Apps → Installed apps and the AVARA uninstaller if provided by your release. Mac: quit AVARA and remove its app from Applications. Saved user data may remain.
- Older source ZIPs: use the included UNINSTALL_AVARA_WINDOWS.bat/UNINSTALL_AVARA.ps1 only for that installation. Do not remove shared Python, Node.js, or Ollama just to uninstall AVARA.

Before relying on optional features, verify them in Settings/Diagnostics and perform a small real action. A successful app launch does not prove every connected service works.

There are distinct download filenames for Basic, Pro, 6-Month Basic and 6-Month Pro. They share the verified app engine; paid server approval determines actual features and license duration.
