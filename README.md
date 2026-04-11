# FMI Upload Guard

`fmi-excel-guard` is a separate Streamlit app for validating uploaded FMI Word documents or pasted article text before content is published.

It stays intentionally strict:

- glaring number inconsistencies
- million / billion unit mistakes
- wrong company names
- wrong or fabricated company developments

It avoids noisy segmentation-only mismatch flags unless they clearly prove a factual error.

## Run Locally

```bash
pip install -e .
set OPENAI_API_KEY=your_key_here
set FMI_APP_PASSWORD=your_shared_password
streamlit run app.py
```

## Output

The app shows per-market findings on screen and lets the user download a Word document with:

- Market Name
- Find
- Replace with
- Why flagged

## Input Rules

- Upload up to 5 `.docx` files at a time.
- Or paste 1 article up to 5,000 words.
- The OpenAI API key is read from server-side environment variables or Streamlit secrets, not from the UI.
- Login is restricted to `@futuremarketinsights.com` emails plus a shared app password from `FMI_APP_PASSWORD`.
- The checker stays strict and avoids segmentation-only mismatch noise.

## Streamlit Community Cloud

Deploy this repository by pointing Streamlit Community Cloud at the repo root and setting these app secrets:

```toml
OPENAI_API_KEY = "your_openai_key"
FMI_APP_PASSWORD = "your_shared_password"
```

If you prefer environment variables outside Streamlit secrets, set the same values in the deployment environment.

## Sleep Behavior

Streamlit Community Cloud can still suspend inactive apps. There is no reliable in-app workaround that honestly guarantees "never sleep" on the free/community tier. If always-on availability is required, use an always-on host or a paid tier instead of trying to fake activity.
