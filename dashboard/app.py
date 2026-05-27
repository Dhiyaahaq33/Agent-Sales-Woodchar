"""Streamlit Dashboard — PT Cahaya Woodchar International AI Sales Agent v2.

Jalankan:  streamlit run dashboard/app.py
Akses di:  http://localhost:8501
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import sqlite3
from datetime import datetime, timedelta, date

import pandas as pd
import streamlit as st

import config

st.set_page_config(
    page_title="Cahaya Woodchar — Sales Dashboard",
    page_icon="🪵",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── DB helpers ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def query(sql: str, params=()) -> pd.DataFrame:
    try:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        df = pd.read_sql_query(sql, conn, params=params)
        conn.close()
        return df
    except Exception as e:
        return pd.DataFrame()


# ── Sidebar navigation ────────────────────────────────────────────────────────
st.sidebar.title("🪵 Cahaya Woodchar")
st.sidebar.caption("AI Sales Agent v2.0")
page = st.sidebar.radio(
    "Navigation",
    ["🏠 Dashboard", "📋 Leads", "📧 Outreach", "📊 Analytics", "🚨 Errors"],
)

# ── Page: Dashboard ───────────────────────────────────────────────────────────
if page == "🏠 Dashboard":
    st.title("🪵 PT Cahaya Woodchar International")
    st.subheader("Sales Pipeline Dashboard")

    today = date.today().isoformat()
    col1, col2, col3, col4 = st.columns(4)

    leads_today = query("SELECT COUNT(*) as n FROM leads WHERE DATE(created_at)=?", (today,))
    emails_today = query("SELECT COUNT(*) as n FROM outreach WHERE DATE(sent_at)=?", (today,))
    replies_today = query("SELECT COUNT(*) as n FROM outreach WHERE DATE(sent_at)=? AND replied=1", (today,))
    interested_total = query("SELECT COUNT(*) as n FROM leads WHERE classification='INTERESTED' OR status='QUALIFIED'")

    col1.metric("Leads Hari Ini", leads_today["n"].iloc[0] if not leads_today.empty else 0)
    col2.metric("Email Terkirim", emails_today["n"].iloc[0] if not emails_today.empty else 0)
    col3.metric("Balasan Hari Ini", replies_today["n"].iloc[0] if not replies_today.empty else 0)
    col4.metric("Total Interested", interested_total["n"].iloc[0] if not interested_total.empty else 0)

    st.divider()

    col_funnel, col_recent = st.columns([1, 1])

    with col_funnel:
        st.subheader("Pipeline Funnel")
        pipeline = query("SELECT status, COUNT(*) as count FROM leads GROUP BY status ORDER BY count DESC")
        if not pipeline.empty:
            import plotly.express as px
            fig = px.bar(pipeline, x="count", y="status", orientation="h",
                         color="status", title="Leads by Status")
            st.plotly_chart(fig, use_container_width=True)

    with col_recent:
        st.subheader("Leads Terbaru")
        recent = query(
            "SELECT company_name, country, status, score FROM leads ORDER BY id DESC LIMIT 10"
        )
        if not recent.empty:
            st.dataframe(recent, use_container_width=True, hide_index=True)

    # INTERESTED leads
    st.subheader("🔥 INTERESTED Buyers")
    interested = query(
        """SELECT company_name, country, email, score, notes, updated_at
           FROM leads WHERE classification='INTERESTED' OR status='QUALIFIED'
           ORDER BY updated_at DESC"""
    )
    if interested.empty:
        st.info("Belum ada INTERESTED leads.")
    else:
        st.dataframe(interested, use_container_width=True, hide_index=True)

# ── Page: Leads ───────────────────────────────────────────────────────────────
elif page == "📋 Leads":
    st.title("📋 Leads Database")

    col1, col2, col3 = st.columns(3)
    status_filter = col1.selectbox("Status", ["ALL", "NEW", "ENRICHED", "OUTREACH",
                                               "REPLIED", "QUALIFIED", "DEAD"])
    country_filter = col2.text_input("Filter Country", "")
    score_min = col3.slider("Min Score", 0, 100, 0)

    sql = "SELECT * FROM leads WHERE score >= ?"
    params = [score_min]
    if status_filter != "ALL":
        sql += " AND status=?"
        params.append(status_filter)
    if country_filter:
        sql += " AND LOWER(country) LIKE ?"
        params.append(f"%{country_filter.lower()}%")
    sql += " ORDER BY updated_at DESC LIMIT 500"

    df = query(sql, params)
    st.caption(f"{len(df)} leads ditemukan")
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False)
        st.download_button("⬇ Export CSV", csv, "leads_export.csv", "text/csv")

# ── Page: Outreach ────────────────────────────────────────────────────────────
elif page == "📧 Outreach":
    st.title("📧 Outreach Log")

    outreach = query(
        """SELECT o.id, l.company_name, l.country, o.touch_number,
                  o.subject, o.sent_at, o.replied, o.channel
           FROM outreach o JOIN leads l ON o.lead_id=l.id
           ORDER BY o.sent_at DESC LIMIT 200"""
    )
    if not outreach.empty:
        st.dataframe(outreach, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📈 Emails Sent — Last 14 Days")
    trend = query(
        """SELECT DATE(sent_at) as day, COUNT(*) as sent,
                  SUM(replied) as replies
           FROM outreach WHERE sent_at >= datetime('now', '-14 days')
           GROUP BY day ORDER BY day"""
    )
    if not trend.empty:
        import plotly.express as px
        fig = px.line(trend, x="day", y=["sent", "replies"], title="Email Activity")
        st.plotly_chart(fig, use_container_width=True)

# ── Page: Analytics ───────────────────────────────────────────────────────────
elif page == "📊 Analytics":
    st.title("📊 Analytics")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Leads by Country")
        by_country = query(
            "SELECT country, COUNT(*) as count FROM leads WHERE country != '' GROUP BY country ORDER BY count DESC LIMIT 15"
        )
        if not by_country.empty:
            import plotly.express as px
            fig = px.pie(by_country, names="country", values="count", title="Country Distribution")
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Score Distribution")
        scores = query("SELECT score FROM leads WHERE score > 0")
        if not scores.empty:
            import plotly.express as px
            fig = px.histogram(scores, x="score", nbins=20, title="Lead Score Distribution")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("A/B Test Results")
    ab = query("SELECT * FROM ab_tests ORDER BY id DESC LIMIT 5")
    if not ab.empty:
        st.dataframe(ab, use_container_width=True, hide_index=True)
    else:
        st.info("Belum ada A/B test data.")

    st.subheader("Classification Breakdown")
    cls = query(
        "SELECT classification, COUNT(*) as count FROM leads WHERE classification IS NOT NULL GROUP BY classification"
    )
    if not cls.empty:
        import plotly.express as px
        fig = px.bar(cls, x="classification", y="count", title="Reply Classification")
        st.plotly_chart(fig, use_container_width=True)

# ── Page: Errors ──────────────────────────────────────────────────────────────
elif page == "🚨 Errors":
    st.title("🚨 Error Log")
    log_path = config.LOGS_DIR / "agent.log"
    if not log_path.exists():
        st.warning("Log file tidak ditemukan.")
    else:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            errors = [l for l in lines if "ERROR" in l or "CRITICAL" in l or "WARNING" in l][-100:]
            if errors:
                severity = st.selectbox("Filter", ["ALL", "ERROR", "CRITICAL", "WARNING"])
                filtered = [l for l in errors if severity == "ALL" or severity in l]
                st.code("\n".join(filtered[-50:]), language=None)
            else:
                st.success("✅ Tidak ada error dalam log terakhir.")
        except Exception as e:
            st.error(f"Gagal baca log: {e}")

st.sidebar.divider()
st.sidebar.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()
