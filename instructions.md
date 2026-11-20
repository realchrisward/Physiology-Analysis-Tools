# Installing and running Physiology Analysis Tools

Three steps: **download** the app, **install** it once, then **start** it whenever you need it.

---

## 1. Download the app

Download this ZIP file and unzip it somewhere easy to find, for example your **Documents** folder:

**https://github.com/realchrisward/Physiology-Analysis-Tools/archive/refs/heads/web-ui-redesign.zip**

You now have a folder called `Physiology-Analysis-Tools-web-ui-redesign`. Everything below happens inside that folder.

> Using Git? `git clone -b web-ui-redesign https://github.com/realchrisward/Physiology-Analysis-Tools.git` works too.

---

## 2. Install (first time only)

You need an internet connection. The first install takes **10–20 minutes**.

### Windows

1. Open the app folder, then the **`installation`** folder inside it.
2. Double-click **`install.bat`**.
3. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
4. Answer the questions with **Y** and Enter (or just Enter).

### macOS

1. Open **Terminal** (press Cmd + Space, type *Terminal*, press Enter).
2. Type `cd ` (with a space after it), drag the app folder into the Terminal window, and press Enter.
3. Type `bash installation/install.sh` and press Enter.
4. Answer the questions with **Y** and Enter (or just Enter).

### What you will see

The installer works through five steps and prints **OK** in green after each one:

| Step | What it does | What to know |
|---|---|---|
| 1. Checking for Python 3.12 | Looks for Python, the program that runs the analysis | If it's missing, the installer offers to install it. Say **Y**. |
| 2. Checking for Node.js | Looks for Node.js, which runs the app window | Same: if it's missing, say **Y** to install it. |
| 3. Installing the Python packages | Downloads the analysis tools into a hidden `.venv` folder | Takes the longest. Lots of scrolling text is normal. |
| 4. Installing the app packages | Downloads the app window and screens | A few minutes. Warnings in the text are normal. |
| 5. Done | Tells you how to start the app | It asks whether to start the app now. |

If something goes wrong you'll see a red **X** with what to do. Fix that, then run the installer again. It keeps what already worked, so the next run is much quicker.

### If Python or Node.js can't be installed automatically

The installer then tells you where to get them. Install them yourself, close the window, and run the installer again.

| Program | Where to get it | What to choose |
|---|---|---|
| Python 3.12 | https://www.python.org/downloads/ | The latest **3.12** version for your system (not 3.13 or newer). On Windows, keep the "py launcher" option ticked. |
| Node.js | https://nodejs.org/ | The **LTS** version. The default installer options are fine. |

---

## 3. Start the app

- **Windows:** double-click **`start-app.bat`** in the app folder.
- **macOS:** double-click **`start-app.command`**. If macOS won't open it, run `bash start-app.command` in Terminal from the app folder.

A text window opens first, then the app window. **Keep the text window open** while you use the app; closing it stops the app.

The first start can take a minute or two. After that it is quick.

---

## Updating to a newer version

Download and unzip the new version (step 1), then run the installer again (step 2). Your saved review work is kept: it isn't stored in the app folder.

---

## If something isn't working

| Problem | What to do |
|---|---|
| The app says the backend did not start in time | Close it and start it again. The first start after turning the computer on can be slow. |
| "Python virtual environment not found" | The install didn't finish. Run the installer again. |
| `.adicht` (LabChart) files don't open on a Mac | They only open on Windows. Export them from LabChart as `.txt`, `.edf` or `.mat` instead. |
| Anything else | Copy the text from the installer or start window and send it to the person who supports the app. |

---

## Where your work is saved

Your review work (beats, arrhythmia decisions, bad-data marks) is saved automatically in a folder called `.physiology_analysis_tools` in your user folder. It is not in the app folder, so deleting or updating the app doesn't remove it. **Start fresh** in the app clears it for one recording.

---

## For developers

| Task | Command (in the app folder) |
|---|---|
| Start in development mode | `npm run dev` |
| Frontend and desktop tests | `npm test` |
| Type check | `npm run check` |
| Backend tests | Windows: `.venv\Scripts\python -m pytest backend/tests -q` · macOS: `.venv/bin/python -m pytest backend/tests -q` |
