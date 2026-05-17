# District.in Telegram Alert for India's Got Latent

This project monitors the District artist page for Samay Raina and sends a Telegram alert when a new event URL containing `latent` appears.

It is designed to be:

- 100% free
- cloud-based
- GitHub Actions scheduled
- stateful between runs
- duplicate-safe
- browser-free

## File Structure

```text
district-alert/
├── .github/
│   └── workflows/
│       └── check.yml
├── check.py
├── README.md
├── requirements.txt
└── state.json
```

`state.json` is created automatically on the first successful run.

## How It Works

1. GitHub Actions runs every 10 minutes.
2. `check.py` downloads `https://www.district.in/events/samay-raina/artist`.
3. The script extracts all `/events/...` URLs from the HTML.
4. It filters only URLs containing `latent` case-insensitively.
5. On the first run, it saves the current matches and does not send alerts.
6. On later runs, it compares the latest list with `state.json`.
7. If a new latent event URL appears, it sends a Telegram message and updates state.

## Telegram Message Format

```text
🚨 INDIA'S GOT LATENT ALERT 🚨

New event found:
https://www.district.in/events/xxxxx
```

If multiple new URLs appear in the same run, they are all included in the same message.

## Setup Instructions

### 1. Create the GitHub Repository

Create a new GitHub repository and push the `district-alert` folder contents so that `check.py` and `.github/workflows/check.yml` are at the repository root.

In other words, the repository root should contain:

```text
.github/workflows/check.yml
check.py
README.md
requirements.txt
```

If you keep `district-alert` as a subfolder inside a larger repo, GitHub Actions will not detect the workflow. Make `district-alert` the root of the GitHub repo.

### 2. Create a Telegram Bot with `@BotFather`

1. Open Telegram and search for `@BotFather`.
2. Start a chat and send `/newbot`.
3. Choose a display name for your bot.
4. Choose a unique bot username that ends with `bot`.
5. BotFather will return a bot token that looks like:

```text
1234567890:AAExampleTokenHere
```

Save this token. You will use it as `BOT_TOKEN`.

### 3. Get Your Telegram Chat ID

1. Open Telegram and start a chat with your new bot.
2. Send any message to the bot, such as `hello`.
3. In a browser, open this URL and replace `<BOT_TOKEN>`:

```text
https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
```

4. Find the `chat` object in the JSON response.
5. Copy the numeric `id` field.

Example:

```json
{
  "message": {
    "chat": {
      "id": 123456789,
      "type": "private"
    }
  }
}
```

In this example, `123456789` is your `CHAT_ID`.

If `getUpdates` returns an empty result, send another message to your bot and refresh the URL.

### 4. Add GitHub Repository Secrets

In your GitHub repository:

1. Go to `Settings`.
2. Go to `Secrets and variables` > `Actions`.
3. Click `New repository secret`.
4. Add these two secrets exactly:

- `BOT_TOKEN`: your Telegram bot token
- `CHAT_ID`: your Telegram chat ID

### 5. Enable GitHub Actions

1. Open the repository on GitHub.
2. Click the `Actions` tab.
3. If GitHub asks you to enable workflows, enable them.

The workflow uses:

- a scheduled trigger every 10 minutes
- a manual trigger for testing
- `contents: write` permission so it can commit `state.json`

### 6. Manually Test the Workflow

1. Open the `Actions` tab.
2. Select `District Latent Monitor`.
3. Click `Run workflow`.
4. Choose the default branch.
5. Click `Run workflow`.

Expected behavior:

- first successful run creates `state.json`
- first run does not send alerts for existing latent URLs
- later runs only send alerts for newly discovered latent URLs

### 7. Verify State Persistence

After the first successful run:

1. Open the repository on GitHub.
2. Confirm that `state.json` was committed by `github-actions[bot]`.
3. Confirm the file contains saved latent event URLs and timestamps.

## Required Environment Variables

These are read from GitHub Actions secrets:

- `BOT_TOKEN`
- `CHAT_ID`

## Failure Handling

The script is designed to handle these cases safely:

- no events found: it saves an empty latent list without crashing
- first run: seeds state without sending a notification
- network timeout: workflow fails and retries on the next run
- malformed HTML: parser falls back to regex-based extraction
- duplicate latent URLs: duplicates are removed before comparison
- Telegram API failure: workflow fails and state is not advanced for unseen URLs

## Local Run

You can test locally with:

```bash
pip install -r requirements.txt
BOT_TOKEN=your_bot_token CHAT_ID=your_chat_id python check.py
```

If `state.json` does not exist, the first run will create it and suppress alerts.

## Notes

- GitHub Actions scheduled workflows are best-effort, not real-time guaranteed.
- The cron is set to every 10 minutes as requested.
- The parser uses both BeautifulSoup and regex so it is more resilient to markup changes.
