from __future__ import annotations

from collections import defaultdict
import json
import re
import time

import streamlit as st
import streamlit.components.v1 as components

from fmi_excel_guard.auth import ALLOWED_DOMAIN, get_app_password, is_allowed_email
from fmi_excel_guard.config import get_openai_api_key, get_openai_model
from fmi_excel_guard.openai_review import apply_openai_checks
from fmi_excel_guard.reporting import build_findings_docx, findings_to_dataframe
from fmi_excel_guard.rules import run_rule_checks
from fmi_excel_guard.word_parser import load_market_record_from_text, load_market_records_from_word_files


def _uploaded_file_key(files: list) -> tuple[tuple[str, int], ...]:
    return tuple((file.name, len(file.getvalue())) for file in files)


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _reset_for_next_run() -> None:
    st.session_state["input_key"] = None
    st.session_state["findings"] = None
    st.session_state["run_error"] = None
    st.session_state["run_summary"] = None
    st.session_state["run_status"] = None
    st.session_state["active_run"] = False
    st.session_state["ui_nonce"] = st.session_state.get("ui_nonce", 0) + 1


FACTS = [
    {
        "fact": "Around 6 billion people, roughly three-quarters of the world, are using the Internet in 2025.",
        "source": "ITU, Facts and Figures 2025",
        "url": "https://www.itu.int/en/mediacentre/Pages/PR-2025-11-17-Facts-and-Figures.aspx",
    },
    {
        "fact": "About 3 billion 5G subscriptions now account for roughly one-third of all mobile broadband subscriptions worldwide.",
        "source": "ITU, Facts and Figures 2025",
        "url": "https://www.itu.int/en/mediacentre/Pages/PR-2025-11-17-Facts-and-Figures.aspx",
    },
    {
        "fact": "In OECD economies, small and medium-sized enterprises account for over 99% of companies and 60% of business sector employment.",
        "source": "OECD, Generative AI and the SME Workforce",
        "url": "https://www.oecd.org/en/publications/generative-ai-and-the-sme-workforce_2d08b99d-en/full-report/component-3.html",
    },
    {
        "fact": "About 56% of the world's population, or 4.4 billion people, live in cities today.",
        "source": "World Bank Urban Development",
        "url": "https://ppp.worldbank.org/library/world-bank-urban-development-website",
    },
    {
        "fact": "More than 80% of global GDP is generated in cities.",
        "source": "World Bank Urban Development",
        "url": "https://ppp.worldbank.org/library/world-bank-urban-development-website",
    },
    {
        "fact": "The global fiscal deficit averaged 5.1% of GDP in 2024.",
        "source": "IMF, Rising Debt Levels and Fiscal Adjustments",
        "url": "https://www.imf.org/external/pubs/ft/ar/2025/in-focus/rising-debt-levels-and-fiscal-adjustments/",
    },
    {
        "fact": "Global public debt rose to 92.3% of GDP in 2024.",
        "source": "IMF, Rising Debt Levels and Fiscal Adjustments",
        "url": "https://www.imf.org/external/pubs/ft/ar/2025/in-focus/rising-debt-levels-and-fiscal-adjustments/",
    },
]


@st.cache_data(show_spinner=False, ttl=3600)
def _load_trivia_facts(limit: int = 120) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    for value in range(1, limit + 1):
        try:
            import urllib.parse
            import urllib.request

            url = f"https://numbersapi.com/{value}/trivia?json"
            with urllib.request.urlopen(url, timeout=4) as response:
                payload = json.loads(response.read().decode("utf-8"))
            text = str(payload.get("text", "")).strip()
            if text:
                facts.append(
                    {
                        "fact": text,
                        "source": "Numbers API",
                        "url": f"https://numbersapi.com/{value}/trivia",
                    }
                )
        except Exception:
            continue
    return facts or FACTS


TRIVIA_FACTS = _load_trivia_facts()


def _render_run_status(status: dict[str, object]) -> str:
    return (
        '<div class="progress-card">'
        '<strong>Run status</strong><br>'
        f"Stage: {status.get('stage', 'Preparing run')}<br>"
        f"Current section: {status.get('current_section', 'Waiting to start')}<br>"
        f"Elapsed: {status.get('elapsed_seconds', 0.0):.1f}s<br>"
        f"ETA: {status.get('eta_seconds', 0.0):.1f}s<br>"
        f"Findings so far: {status.get('findings_so_far', 0)}<br>"
        f"Tokens used so far: {status.get('tokens_used_so_far', 0):,}"
        "</div>"
    )


def _render_fact_rotator() -> None:
    fact_payload = json.dumps(TRIVIA_FACTS)
    components.html(
        f"""
        <div id="fact-panel" style="
            background: rgba(255,255,255,0.94);
            border: 1px solid rgba(82, 141, 201, 0.16);
            border-radius: 18px;
            padding: 18px 20px;
            box-shadow: 0 18px 44px rgba(49, 90, 130, 0.08);
            font-family: Segoe UI, Arial, sans-serif;
            min-height: 140px;
        ">
          <div style="font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:#2b6ea6; font-weight:700; margin-bottom:10px;">
            While you wait
          </div>
          <div id="fact-text" style="font-size:20px; line-height:1.45; color:#14324a; font-weight:700; margin-bottom:10px;"></div>
          <div style="font-size:13px; color:#567086;">
            Source:
            <a id="fact-link" href="#" target="_blank" style="color:#1f6fb5; text-decoration:none; font-weight:600;"></a>
          </div>
        </div>
        <script>
          const facts = {fact_payload};
          const factText = document.getElementById("fact-text");
          const factLink = document.getElementById("fact-link");
          let index = 0;
          function renderFact() {{
            const item = facts[index];
            factText.textContent = "Did you know? " + item.fact;
            factLink.textContent = item.source;
            factLink.href = item.url;
            index = (index + 1) % facts.length;
          }}
          renderFact();
          setInterval(renderFact, 5000);
        </script>
        """,
        height=160,
    )


def _render_login_gate() -> None:
    st.markdown(
        """
        <div class="hero-card">
          <div class="eyebrow">Secure Access</div>
          <h1 class="hero-title">Sign in to FMI Upload Guard</h1>
          <p class="hero-copy">
            Access is limited to official company email addresses ending with @futuremarketinsights.com.
            Enter your work email and the internal access password.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    email = st.text_input("Official work email", placeholder="name@futuremarketinsights.com")
    password = st.text_input("Access password", type="password", placeholder="Enter shared internal password")
    configured_password = get_app_password()

    if st.button("Sign in", use_container_width=True):
        normalized = email.strip().lower()
        if not is_allowed_email(normalized):
            st.session_state["auth_error"] = f"Only @{ALLOWED_DOMAIN} email addresses are allowed."
        elif not configured_password:
            st.session_state["auth_error"] = "FMI_APP_PASSWORD is not configured on this machine."
        elif password != configured_password:
            st.session_state["auth_error"] = "Incorrect password."
        else:
            st.session_state["authenticated"] = True
            st.session_state["authenticated_email"] = normalized
            st.session_state["auth_error"] = None
            st.session_state["auth_message"] = "Sign-in successful."
        st.rerun()

    auth_error = st.session_state.get("auth_error")
    if auth_error:
        st.error(auth_error)


st.set_page_config(page_title="FMI Upload Guard", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
    .stApp {
        background:
            radial-gradient(circle at top left, rgba(117, 182, 255, 0.22), transparent 30%),
            radial-gradient(circle at top right, rgba(200, 231, 255, 0.55), transparent 32%),
            linear-gradient(180deg, #f7fbff 0%, #edf5fb 100%);
        color: #12344d;
    }
    .block-container {
        max-width: 1180px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    .hero-card, .metric-card {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid rgba(82, 141, 201, 0.16);
        border-radius: 22px;
        box-shadow: 0 18px 44px rgba(49, 90, 130, 0.08);
    }
    .hero-card { padding: 1.5rem 1.6rem; margin-bottom: 1rem; }
    .metric-card { padding: 1rem 1.1rem; }
    .input-card {
        background: rgba(255, 255, 255, 0.92);
        border: 1px solid rgba(82, 141, 201, 0.16);
        border-radius: 18px;
        padding: 1rem 1.1rem;
        margin-bottom: 1rem;
    }
    .eyebrow {
        display:inline-block;
        font-size: 0.76rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #2b6ea6;
        font-weight: 700;
        margin-bottom: 0.7rem;
    }
    .hero-title {
        font-size: 2.2rem;
        line-height: 1.05;
        font-weight: 800;
        color: #14324a;
        margin: 0 0 0.7rem 0;
    }
    .hero-copy {
        font-size: 1rem;
        color: #46637c;
        margin: 0;
        max-width: 62rem;
    }
    .metric-label {
        color: #5f7b92;
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-weight: 700;
        margin-bottom: 0.35rem;
    }
    .metric-value {
        color: #14324a;
        font-size: 1.5rem;
        font-weight: 800;
        margin: 0;
    }
    .token-strip {
        background: rgba(255, 255, 255, 0.94);
        border: 1px solid rgba(82, 141, 201, 0.16);
        border-radius: 18px;
        padding: 0.95rem 1.05rem;
        margin: 1rem 0 0.7rem 0;
        color: #14324a;
    }
    .stTextArea textarea, .stTextInput input {
        background: rgba(250, 253, 255, 0.95);
        color: #14324a;
        border-radius: 14px;
        border: 1px solid rgba(82, 141, 201, 0.24);
    }
    .stButton button, .stDownloadButton button {
        background: linear-gradient(180deg, #1f7acb 0%, #165f9f 100%);
        color: white;
        border: none;
        border-radius: 12px;
        font-weight: 700;
    }
    .stDownloadButton button {
        min-height: 3.25rem;
        font-size: 1rem;
        box-shadow: 0 14px 28px rgba(22, 95, 159, 0.18);
    }
    .stButton button:hover, .stDownloadButton button:hover {
        background: linear-gradient(180deg, #1768b0 0%, #124e83 100%);
        color: white;
    }
    .finding-find {
        background: #eef7ff;
        border-left: 4px solid #2b6ea6;
        padding: 0.75rem 0.9rem;
        border-radius: 10px;
        margin-bottom: 0.75rem;
    }
    .finding-replace {
        background: #f6fbff;
        border-left: 4px solid #4d9de0;
        padding: 0.75rem 0.9rem;
        border-radius: 10px;
        margin-bottom: 0.75rem;
    }
    .progress-card {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid rgba(82, 141, 201, 0.16);
        border-radius: 18px;
        padding: 1rem 1.05rem;
        margin: 0.8rem 0 1rem 0;
        color: #14324a;
    }
    .nav-row {
        display: flex;
        gap: 0.75rem;
        margin: 0.25rem 0 1rem 0;
    }
    div[data-baseweb="tab-list"] {
        gap: 0.5rem;
    }
    button[data-baseweb="tab"] {
        background: rgba(255, 255, 255, 0.92);
        border: 1px solid rgba(82, 141, 201, 0.16);
        border-radius: 12px;
        color: #1f4e74;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

server_api_key = get_openai_api_key()
if not server_api_key:
    try:
        if "OPENAI_API_KEY" in st.secrets:
            server_api_key = str(st.secrets["OPENAI_API_KEY"]).strip() or None
    except Exception:
        server_api_key = None
openai_model = get_openai_model()

if not st.session_state.get("authenticated"):
    _render_login_gate()
    st.stop()

st.markdown(
    """
    <div class="hero-card">
      <div class="eyebrow">Internal Review Tool</div>
      <h1 class="hero-title">FMI Upload Guard</h1>
      <p class="hero-copy">
        Upload up to five Word documents or paste one article and run the same strict glaring-error checks used in FMI Guard.
        The app only flags high-signal issues such as obvious number inconsistencies, million or billion mistakes,
        wrong company names, and wrong company developments.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption(f"Signed in as {st.session_state.get('authenticated_email', '')}")

nav_col1, nav_col2, nav_col3 = st.columns([1, 1, 3])
with nav_col1:
    if st.button("Home", use_container_width=True):
        _reset_for_next_run()
        st.rerun()
with nav_col2:
    if st.button("QC next article(s)", use_container_width=True):
        _reset_for_next_run()
        st.rerun()

ui_nonce = st.session_state.get("ui_nonce", 0)
st.session_state.setdefault("active_run", False)

tab_upload, tab_paste = st.tabs(["Upload Word documents", "Paste article text"])

records = []
preview_rows = []
input_key = None

with tab_upload:
    st.markdown('<div class="input-card">', unsafe_allow_html=True)
    uploaded_files = st.file_uploader(
        "Upload Word documents",
        type=["docx"],
        accept_multiple_files=True,
        help="Upload up to five market documents at a time.",
        key=f"upload_files_{ui_nonce}",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    if uploaded_files and len(uploaded_files) > 5:
        st.error("Upload a maximum of 5 Word documents at a time.")
        st.stop()

    if uploaded_files:
        input_key = ("upload", _uploaded_file_key(uploaded_files))
        with st.spinner("Parsing Word documents..."):
            records = load_market_records_from_word_files(
                [(file.name, file.getvalue()) for file in uploaded_files]
            )
        preview_rows = [
            {
                "Item": uploaded_files[index].name,
                "Market Name": record.market_name,
                "Primary summary": record.meta_desc[:180] + ("..." if len(record.meta_desc) > 180 else ""),
            }
            for index, record in enumerate(records)
        ]

with tab_paste:
    st.markdown('<div class="input-card">', unsafe_allow_html=True)
    pasted_title = st.text_input(
        "Article name",
        placeholder="Optional. Example: Abrasive Base Paper Market",
        key=f"pasted_title_{ui_nonce}",
    )
    pasted_text = st.text_area(
        "Paste one article",
        height=320,
        placeholder="Paste one article here. Maximum 5,000 words.",
        key=f"pasted_text_{ui_nonce}",
    )
    pasted_word_count = _count_words(pasted_text)
    st.caption(f"Word count: {pasted_word_count} / 5000")
    st.markdown("</div>", unsafe_allow_html=True)

    if pasted_word_count > 5000:
        st.error("Pasted article exceeds the 5,000-word limit.")
        st.stop()

    if pasted_text.strip():
        record = load_market_record_from_text(
            text=pasted_text,
            title=pasted_title.strip() or "Pasted Article",
        )
        records = [record]
        input_key = ("paste", pasted_title.strip(), pasted_word_count, hash(pasted_text.strip()))
        preview_rows = [
            {
                "Item": pasted_title.strip() or "Pasted Article",
                "Market Name": record.market_name,
                "Primary summary": record.meta_desc[:180] + ("..." if len(record.meta_desc) > 180 else ""),
            }
        ]

if input_key is not None:
    if st.session_state.get("input_key") != input_key:
        st.session_state["input_key"] = input_key
        st.session_state["findings"] = None
        st.session_state["run_error"] = None
        st.session_state["run_summary"] = None

    metric_cols = st.columns(2)
    metrics = [
        ("Items loaded", str(len(records))),
        ("Run limit", "5 docs or 1 pasted article"),
    ]
    for column, (label, value) in zip(metric_cols, metrics):
        with column:
            st.markdown(
                f'<div class="metric-card"><div class="metric-label">{label}</div><p class="metric-value">{value}</p></div>',
                unsafe_allow_html=True,
            )

    st.markdown("### Items in this run")
    st.dataframe(preview_rows, use_container_width=True, hide_index=True)

    if st.button("Run glaring-error checks", type="primary", use_container_width=True):
        if not server_api_key:
            st.session_state["findings"] = None
            st.session_state["run_error"] = (
                "AI review is not configured on this machine. Add OPENAI_API_KEY to the server "
                "environment or Streamlit secrets, then run again."
            )
            st.session_state["run_summary"] = None
            st.rerun()

        findings = []
        st.session_state["active_run"] = True
        progress = st.progress(0.0)
        status = st.empty()
        detail_status = st.empty()
        token_placeholder = st.empty()
        fact_placeholder = st.empty()
        total = len(records)
        ai_checked = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        finding_count = 0

        try:
            run_started_at = time.perf_counter()
            for index, record in enumerate(records, start=1):
                status.info(f"Checking {record.market_name} ({index}/{total})")
                current_status = {
                    "stage": "Running rule checks",
                    "current_section": f"{record.market_name} | document {index} of {total}",
                    "elapsed_seconds": time.perf_counter() - run_started_at,
                    "eta_seconds": 0.0,
                    "findings_so_far": finding_count,
                    "tokens_used_so_far": total_tokens,
                }
                st.session_state["run_status"] = current_status
                detail_status.markdown(_render_run_status(current_status), unsafe_allow_html=True)
                market_findings = run_rule_checks(record)
                if server_api_key:
                    ai_checked += 1
                    def update_usage(chunk_index: int, total_chunks: int, usage) -> None:
                        elapsed = time.perf_counter() - run_started_at
                        overall_completed = (index - 1) + (chunk_index / max(total_chunks, 1))
                        rate = elapsed / max(overall_completed, 0.01)
                        estimated_remaining = max((total - overall_completed) * rate, 0)
                        findings_text = (
                            "Yes, already found some errors."
                            if finding_count + len(market_findings) > 0
                            else "No glaring errors found yet. Still checking."
                        )
                        current_status = {
                            "stage": "Running AI review",
                            "current_section": (
                                f"{record.market_name} | chunk {chunk_index} of {total_chunks} | {findings_text}"
                            ),
                            "elapsed_seconds": elapsed,
                            "eta_seconds": estimated_remaining,
                            "findings_so_far": finding_count + len(market_findings),
                            "tokens_used_so_far": total_tokens + usage.total_tokens,
                        }
                        st.session_state["run_status"] = current_status
                        detail_status.markdown(_render_run_status(current_status), unsafe_allow_html=True)
                        token_placeholder.markdown(
                            (
                                '<div class="token-strip">'
                                f"<strong>Live token usage</strong><br>"
                                f"Document: {record.market_name}<br>"
                                f"Chunk: {chunk_index} of {total_chunks}<br>"
                                f"Input tokens: {total_input_tokens + usage.input_tokens:,} | "
                                f"Output tokens: {total_output_tokens + usage.output_tokens:,} | "
                                f"Total tokens: {total_tokens + usage.total_tokens:,}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )
                        with fact_placeholder.container():
                            _render_fact_rotator()
                    openai_findings, usage = apply_openai_checks(
                        record=record,
                        api_key=server_api_key,
                        model=openai_model,
                        existing_findings=market_findings,
                        progress_callback=update_usage,
                    )
                    total_input_tokens += usage.input_tokens
                    total_output_tokens += usage.output_tokens
                    total_tokens += usage.total_tokens
                    market_findings.extend(openai_findings)
                findings.extend(market_findings)
                finding_count = len(findings)
                progress.progress(index / total)
        except Exception as exc:
            status.empty()
            detail_status.empty()
            st.session_state["findings"] = None
            st.session_state["run_error"] = str(exc)
            st.session_state["run_summary"] = None
            st.session_state["run_status"] = None
            st.session_state["active_run"] = False
        else:
            status.empty()
            detail_status.empty()
            st.session_state["findings"] = findings
            st.session_state["run_error"] = None
            st.session_state["run_summary"] = {
                "documents": total,
                "ai_checked": ai_checked,
                "elapsed_seconds": round(time.perf_counter() - run_started_at, 2),
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
            }
            st.session_state["run_status"] = {
                "stage": "Completed",
                "current_section": "Run finished successfully",
                "elapsed_seconds": round(time.perf_counter() - run_started_at, 2),
                "eta_seconds": 0.0,
                "findings_so_far": len(findings),
                "tokens_used_so_far": total_tokens,
            }
            st.session_state["active_run"] = False

    stored_error = st.session_state.get("run_error")
    if stored_error:
        st.error(f"Run failed: {stored_error}")

    findings = st.session_state.get("findings")
    run_summary = st.session_state.get("run_summary")
    run_status = st.session_state.get("run_status")
    if run_status and st.session_state.get("active_run"):
        st.markdown(_render_run_status(run_status), unsafe_allow_html=True)
    if run_summary:
        st.caption(
            f"Run completed in {run_summary['elapsed_seconds']}s. "
            f"AI reviewed {run_summary['ai_checked']} of {run_summary['documents']} item(s). "
            f"Tokens used: {run_summary['total_tokens']:,} "
            f"({run_summary['input_tokens']:,} in / {run_summary['output_tokens']:,} out)."
        )
    if findings is not None:
        if not findings:
            st.info("No glaring issues were detected in the processed documents.")
        else:
            affected_markets = len({item.market_name for item in findings})
            st.success(f"Detected {len(findings)} findings across {affected_markets} documents.")
            result_df = findings_to_dataframe(findings)
            st.dataframe(result_df, use_container_width=True, hide_index=True)

            docx_bytes = build_findings_docx(findings)
            st.download_button(
                "Download issues and suggested fixes",
                data=docx_bytes,
                file_name="fmi_upload_guard_findings.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )

            grouped: dict[str, list] = defaultdict(list)
            for finding in findings:
                grouped.setdefault(finding.market_name, []).append(finding)

            for market_name in sorted(grouped):
                with st.expander(f"{market_name} ({len(grouped[market_name])} finding(s))", expanded=False):
                    for item in grouped[market_name]:
                        st.markdown(
                            f'<div class="finding-find"><strong>Find</strong><br>{item.find_text}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f'<div class="finding-replace"><strong>Replace with</strong><br>{item.replace_with}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(f"**Why flagged**: {item.why_flagged}")
                        st.markdown(f"**Source**: {item.source}")
                        st.divider()
else:
    st.session_state["input_key"] = None
    st.session_state["findings"] = None
    st.session_state["run_error"] = None
    st.session_state["run_summary"] = None
    st.session_state["active_run"] = False
    st.info("Upload up to 5 Word documents or paste 1 article to begin.")
