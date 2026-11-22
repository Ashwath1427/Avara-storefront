# AVARA setup guide

## Download and install

1. Sign in to the AVARA storefront with the account used for your order. Select Windows or Mac when ordering. Cash payment and approval are handled by the owner.
2. After approval, open **Your orders → Download AVARA**. Mac users choose **Apple Silicon** or **Intel**. Use Apple menu → About This Mac to identify the chip.
3. Windows receives a plan-labelled EXE, such as `AVARA-1.0.1-basic-windows-setup.exe`. Run the installer, then launch AVARA from its installed shortcut.
4. Mac receives a plan-labelled Apple Silicon (`mac-arm64.dmg`) or Intel (`mac-x86_64.dmg`) installer. Open it, drag AVARA to Applications, eject the disk image, and open the installed app.
5. Send the one-time code shown in AVARA and your order number to the owner. Keep AVARA open and online while the owner activates that installation.

The packaged app includes Python and its application dependencies. You do not need to install Python or Node.js separately for the EXE/DMG release. Ollama is a separate optional installation if you choose local AI. A Windows installer cannot run on macOS, and a Mac DMG cannot run on Windows.

## Choose one AI option

- **Groq:** create a key at [Groq API Keys](https://console.groq.com/keys).
- **Gemini:** create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).
- **Local AI:** install [Ollama](https://ollama.com/download), download a compatible model from its [model library](https://ollama.com/library), and keep Ollama running.

Use the welcome setup or **Settings → AI & voice** to choose your provider, save its key or model, and test a simple chat. You need only one AI option. Groq/Gemini require internet and have their own limits or charges. Local models need sufficient computer resources. Optional voice, image, video, Google, and other integrations have separate setup requirements.

Enter API keys only in AVARA's local setup/settings. Never send passwords, activation tokens, or API keys in public messages or screenshots.

## Approval and subscriptions

Monthly Basic is ₹150 and monthly Pro is ₹200. Six-month Basic is ₹1,000 and six-month Pro is ₹1,400. Initial activation lasts 30 or 180 days according to the approved order. Both plans use the same platform installer; the approved order supplies the entitlement.

The first activation and periodic license checks require internet. The owner can pause, continue, renew, cancel, or change the plan. Deleted orders cannot download or activate until restored. Keep your AVARA user-data folder; reinstalling or deleting it can lose the device token and local settings.

## Source ZIPs only

Older source ZIPs require 64-bit Python 3.11 or newer. Use the [Windows](https://www.python.org/downloads/windows/) or [macOS](https://www.python.org/downloads/macos/) Python download page. Use a Python version supported by the release dependencies; the newest version is not automatically compatible with every package. On Windows enable Add Python to PATH. Extract the entire ZIP and follow START_HERE.txt or the supplied START_AVARA_WINDOWS.bat / START_AVARA_MAC.command launcher. These steps do not apply to packaged EXE/DMG downloads.

## Mac permissions and testing

Screen understanding, microphone, camera, and app control may need the corresponding macOS permissions. Review each request yourself. Windows-specific automation is not guaranteed on macOS. Mac availability in the storefront does not mean every feature has been tested on both Mac architectures.

## Troubleshooting and uninstall

- Missing download: refresh orders and sign in to the ordering account again. An approved order is required.
- Mac chip error from an old link: reopen the download; the chip-choice page now provides both choices.
- Activation cannot connect: confirm internet access, wait for the hosting service to wake, retry, and contact support with the order number and a redacted log if it persists.
- AI offline: test your chosen provider, key, quota, or running Ollama model.
- Startup failure: read AVARA's log under Local AppData/AVARA/logs on Windows or Library/Application Support/AVARA/logs on Mac. Share only a redacted error excerpt.
- Unrecognized publisher warnings: verify the file came from your approved AVARA order. Stop if the source is uncertain; do not disable system protection.
- Windows packaged install: use Settings → Apps → Installed apps and the AVARA uninstaller if provided by your release. Mac: quit AVARA and remove its app from Applications. Saved user data may remain.
- Older source ZIPs: use the included UNINSTALL_AVARA_WINDOWS.bat/UNINSTALL_AVARA.ps1 only for that installation. Do not remove shared Python, Node.js, or Ollama just to uninstall AVARA.

Before relying on optional features, verify them in Settings/Diagnostics and perform a small real action. A successful app launch does not prove every connected service works.

There are distinct download filenames for Basic, Pro, 6-Month Basic and 6-Month Pro. They share the verified app engine; paid server approval determines actual features and license duration.
