import os
import ssl
import smtplib
import io
from urllib.parse import urlencode
from email.message import EmailMessage
from datetime import date

import pandas as pd
import streamlit as st
import oracledb
import qrcode

# ============================================================
# CAMPUS EVENT REGISTRATION & TICKET MANAGEMENT SYSTEM
# Phase 2: improved GUI, filters, exports, safer DB handling,
# analytics, and optional payment confirmation emails.
# ============================================================

st.set_page_config(
    page_title="CAMPUS | Event Management",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, rgba(190,0,70,.10), rgba(110,30,100,.07));
        border: 1px solid rgba(190,0,70,.20);
        padding: 16px; border-radius: 14px;
    }
    div.stButton > button[kind="primary"] {
        border-radius: 10px; font-weight: 650;
    }
    section[data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.20);}
    .campus-subtitle {color: #8c8c98; font-size: .96rem;}
</style>
""", unsafe_allow_html=True)

st.title("🎓 CAMPUS Event Registration")
st.markdown('<div class="campus-subtitle">Campus Event Registration & Ticket Management • Phase 2</div>', unsafe_allow_html=True)
st.divider()

# ---------------- DATABASE SETTINGS ----------------
st.sidebar.header("🔐 Oracle Connection")
db_user = st.sidebar.text_input("Username", value="CAMPUS")
db_password = st.sidebar.text_input(
    "Database password",
    type="password",
    value=os.getenv("CAMPUS_DB_PASSWORD", ""),
    help="Enter the password for your Oracle CAMPUS schema. It is not saved by this app.",
)
db_host = st.sidebar.text_input("Host", value=os.getenv("CAMPUS_DB_HOST", "localhost"))
db_port = st.sidebar.number_input("Port", min_value=1, max_value=65535, value=int(os.getenv("CAMPUS_DB_PORT", "1521")))
db_service = st.sidebar.text_input("Service name", value=os.getenv("CAMPUS_DB_SERVICE", "ORCLPDB"))
merchant_upi_id = st.sidebar.text_input(
    "Merchant UPI ID (optional)",
    value=os.getenv("CAMPUS_UPI_ID", ""),
    help="Example: campusmerchant@upi. Leave blank to use a demo-only QR.",
)

def open_connection():
    if not db_password:
        raise RuntimeError("Enter your Oracle database password in the sidebar.")
    dsn = f"{db_host}:{int(db_port)}/{db_service}"
    return oracledb.connect(user=db_user.strip(), password=db_password, dsn=dsn)

def run_query(sql, params=None):
    """Execute a read query and return results as a DataFrame; always close DB resources."""
    conn = open_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params or {})
        if cur.description is None:
            return pd.DataFrame()
        columns = [col[0] for col in cur.description]
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=columns)
    finally:
        cur.close()
        conn.close()

def run_procedure(plsql, params=None):
    """Call a stored procedure. Existing project procedures may COMMIT internally."""
    conn = open_connection()
    cur = conn.cursor()
    try:
        cur.execute(plsql, params or {})
        # Most existing project procedures commit internally; commit also covers
        # procedures that do not. On failure, roll back any uncommitted work.
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

def make_payment_qr(ticket_id, event_name, amount, upi_id=""):
    """Return a PNG QR image. UPI QR is a payment intent, not proof of payment."""
    amount_text = f"{float(amount):.2f}"
    if upi_id and "@" in upi_id:
        payload = "upi://pay?" + urlencode({
            "pa": upi_id.strip(),
            "pn": "CAMPUS Event Registration",
            "am": amount_text,
            "cu": "INR",
            "tn": f"Ticket {int(ticket_id)} - {str(event_name)[:60]}",
        })
        label = "UPI payment-intent QR"
    else:
        payload = (
            "CAMPUS DEMO PAYMENT\n"
            f"Ticket ID: {int(ticket_id)}\n"
            f"Event: {str(event_name)}\n"
            f"Amount: INR {amount_text}\n"
            "DEMO ONLY - NOT A PAYMENT"
        )
        label = "Demo QR (not a payment QR)"

    qr = qrcode.QRCode(version=None, box_size=8, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), label


def show_error(action, exc):
    message = str(exc)
    st.error(f"{action}: {message}")

def csv_download(df, filename, label="⬇️ Download CSV"):
    if df is not None and not df.empty:
        st.download_button(
            label=label,
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=filename,
            mime="text/csv",
        )

def email_setting(key, default=""):
    value = os.getenv(key, "")
    if value:
        return value
    try:
        return str(st.secrets.get(key, default))
    except Exception:
        return default

def send_payment_confirmation(recipient, student_name, ticket_id, event_name, seat_no, amount):
    """Send a receipt after Oracle has recorded the payment. Requires SMTP settings."""
    smtp_host = email_setting("SMTP_HOST")
    smtp_port = int(email_setting("SMTP_PORT", "587") or "587")
    smtp_user = email_setting("SMTP_USER")
    smtp_password = email_setting("SMTP_PASSWORD")
    sender = email_setting("SMTP_FROM", smtp_user)

    if not all([smtp_host, smtp_user, smtp_password, sender]):
        raise RuntimeError(
            "Email is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD and SMTP_FROM in environment variables or Streamlit secrets."
        )
    if not recipient or "@" not in str(recipient):
        raise RuntimeError("Student email is missing or invalid in the STUDENTS table.")

    msg = EmailMessage()
    msg["Subject"] = f"Payment confirmation — CAMPUS Ticket #{ticket_id}"
    msg["From"] = sender
    msg["To"] = str(recipient)
    msg.set_content(f"""Hello {student_name},

Your ticket payment has been recorded successfully.

Payment receipt:
Ticket ID: {ticket_id}
Event: {event_name}
Seat: {seat_no or 'Not assigned'}
Amount: ₹{float(amount):,.2f}
Payment status: PAID

Please keep this message for your records.

Regards,
CAMPUS Event Registration Team
""")
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

def pay_ticket_and_email(ticket_id):
    """Load receipt details, update Oracle, then try to send email."""
    details = run_query("""
        SELECT ticket_id, student_name, email, event_name, seat_no, ticket_price,
               reg_status, payment_status
        FROM student_registrations_view
        WHERE ticket_id = :ticket_id
    """, {"ticket_id": int(ticket_id)})
    if details.empty:
        raise RuntimeError("Ticket was not found.")
    row = details.iloc[0]
    if str(row["REG_STATUS"]).upper() != "CONFIRMED":
        raise RuntimeError("This registration is not confirmed.")
    if str(row["PAYMENT_STATUS"]).upper() == "PAID":
        raise RuntimeError("This ticket is already paid.")
    if str(row["PAYMENT_STATUS"]).upper() == "REFUNDED":
        raise RuntimeError("This ticket was refunded and cannot be paid again.")

    run_procedure("BEGIN pay_ticket(:ticket_id); END;", {"ticket_id": int(ticket_id)})

    # Email failure must not undo a payment that Oracle already committed.
    try:
        send_payment_confirmation(
            recipient=row["EMAIL"],
            student_name=row["STUDENT_NAME"],
            ticket_id=int(row["TICKET_ID"]),
            event_name=row["EVENT_NAME"],
            seat_no=row["SEAT_NO"],
            amount=row["TICKET_PRICE"],
        )
        return True, str(row["EMAIL"])
    except Exception as exc:
        return False, str(exc)

# ---------------- NAVIGATION ----------------
st.sidebar.divider()
st.sidebar.header("📌 CAMPUS MENU")
page = st.sidebar.radio("Navigate", [
    "🏠 Dashboard",
    "📅 Events",
    "📝 Register",
    "🎟️ My Tickets",
    "💳 Pay Ticket",
    "📊 Reports",
    "🧾 Audit Log (Phase 2)",
])

st.sidebar.divider()
st.sidebar.caption("Oracle Database • Python • Streamlit")
st.sidebar.caption("Phase 2: filters • exports • email receipts • audit trail")

# ---------------- DASHBOARD ----------------
if page == "🏠 Dashboard":
    st.header("Welcome to CAMPUS")
    st.write("Manage campus events, registrations, tickets, payment status and reports from one place.")
    try:
        metrics = run_query("""
            SELECT
              (SELECT COUNT(*) FROM events) AS TOTAL_EVENTS,
              (SELECT COUNT(*) FROM students) AS TOTAL_STUDENTS,
              (SELECT COUNT(*) FROM registrations WHERE status='CONFIRMED') AS CONFIRMED_REGISTRATIONS,
              (SELECT COUNT(*) FROM tickets WHERE payment_status='PAID') AS PAID_TICKETS,
              (SELECT NVL(SUM(ticket_price),0) FROM tickets t
                 JOIN registrations r ON r.registration_id=t.registration_id
                WHERE t.payment_status='PAID' AND r.status='CONFIRMED') AS REVENUE
            FROM dual
        """)
        m = metrics.iloc[0]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("📅 Events", f"{int(m['TOTAL_EVENTS']):,}")
        c2.metric("👩‍🎓 Students", f"{int(m['TOTAL_STUDENTS']):,}")
        c3.metric("🎟️ Confirmed", f"{int(m['CONFIRMED_REGISTRATIONS']):,}")
        c4.metric("💳 Paid tickets", f"{int(m['PAID_TICKETS']):,}")
        c5.metric("💰 Revenue", f"₹{float(m['REVENUE']):,.2f}")

        st.subheader("Upcoming and open events")
        events = run_query("""
            SELECT event_id, event_name, event_date, start_time, end_time,
                   club_name, venue_name, available_seats, ticket_price, event_status
            FROM event_details_view
            WHERE event_date >= TRUNC(SYSDATE)
              AND end_time > SYSDATE
            ORDER BY start_time, event_name
        """)
        if events.empty:
            st.info("No upcoming events are scheduled. If these are demo records, run phase2_repair_demo_data.sql in SQL Developer to move past demo events into the future.")
            all_events = run_query("""
                SELECT event_id, event_name, event_date, start_time, end_time,
                       club_name, venue_name, available_seats, ticket_price, event_status
                FROM event_details_view
                ORDER BY event_date DESC, start_time DESC
            """)
            if not all_events.empty:
                with st.expander("View existing events (including past events)"):
                    st.dataframe(all_events.head(10), use_container_width=True, hide_index=True)
        else:
            st.dataframe(events.head(10), use_container_width=True, hide_index=True)
            csv_download(events, "upcoming_events.csv")
    except Exception as exc:
        show_error("Could not load dashboard. Check Oracle connection, schema and views", exc)

# ---------------- EVENTS ----------------
elif page == "📅 Events":
    st.header("📅 Campus Events")
    try:
        df = run_query("""
            SELECT event_id, event_name, event_date,
                   TO_CHAR(start_time,'HH24:MI') || ' - ' || TO_CHAR(end_time,'HH24:MI') AS timing,
                   ticket_price, available_seats, max_seats, club_name, venue_name, event_status
            FROM event_details_view
            ORDER BY event_date, start_time
        """)
        f1, f2, f3 = st.columns([2, 1, 1])
        with f1:
            search = st.text_input("🔎 Search event, club or venue")
        with f2:
            statuses = ["All"] + sorted(df["EVENT_STATUS"].dropna().astype(str).unique().tolist()) if not df.empty else ["All"]
            status_filter = st.selectbox("Status", statuses)
        with f3:
            only_available = st.checkbox("Seats available", value=False)
        filtered = df.copy()
        if search and not filtered.empty:
            mask = (
                filtered["EVENT_NAME"].astype(str).str.contains(search, case=False, na=False)
                | filtered["CLUB_NAME"].astype(str).str.contains(search, case=False, na=False)
                | filtered["VENUE_NAME"].astype(str).str.contains(search, case=False, na=False)
            )
            filtered = filtered[mask]
        if status_filter != "All" and not filtered.empty:
            filtered = filtered[filtered["EVENT_STATUS"] == status_filter]
        if only_available and not filtered.empty:
            filtered = filtered[filtered["AVAILABLE_SEATS"] > 0]
        st.caption(f"Showing {len(filtered)} of {len(df)} events")
        st.dataframe(filtered, use_container_width=True, hide_index=True)
        csv_download(filtered, "campus_events.csv")
    except Exception as exc:
        show_error("Unable to load events", exc)

# ---------------- REGISTER ----------------
elif page == "📝 Register":
    st.header("📝 Register a Student for an Event")
    st.info("The Oracle procedure validates duplicate registrations, seat availability and event status.")
    try:
        students = run_query("SELECT student_id, student_name, email FROM students ORDER BY student_name")
        events = run_query("""
            SELECT event_id, event_name, event_date, available_seats, ticket_price
            FROM event_details_view
            WHERE available_seats > 0
              AND event_status = 'OPEN'
              AND end_time > SYSDATE
            ORDER BY start_time, event_name
        """)
        if students.empty:
            st.warning("No students found. Add students to Oracle before registering.")
        elif events.empty:
            st.warning("No future events are currently open for registration with seats available.")
            st.info("Your Events page may still show past or full events. For the provided demo data, run phase2_repair_demo_data.sql as CAMPUS in SQL Developer, then refresh this page.")
            try:
                status_counts = run_query("""
                    SELECT event_status, COUNT(*) AS event_count,
                           SUM(available_seats) AS seats_available
                    FROM event_details_view
                    GROUP BY event_status
                    ORDER BY event_status
                """)
                if not status_counts.empty:
                    st.caption("Current event availability in Oracle")
                    st.dataframe(status_counts, use_container_width=True, hide_index=True)
            except Exception:
                pass
        else:
            student_options = {
                f"{r.STUDENT_NAME} ({int(r.STUDENT_ID)}) — {r.EMAIL or 'no email'}": int(r.STUDENT_ID)
                for r in students.itertuples()
            }
            event_options = {
                f"{r.EVENT_NAME} | {str(r.EVENT_DATE)[:10]} | {int(r.AVAILABLE_SEATS)} seats | ₹{float(r.TICKET_PRICE):.2f}": int(r.EVENT_ID)
                for r in events.itertuples()
            }
            with st.form("register_form"):
                selected_student = st.selectbox("Student", list(student_options))
                selected_event = st.selectbox("Event", list(event_options))
                confirmed = st.checkbox("I confirm the selected student and event.")
                submitted = st.form_submit_button("🎟️ Register", type="primary", use_container_width=True)
            if submitted:
                if not confirmed:
                    st.warning("Please confirm the selected student and event.")
                else:
                    try:
                        run_procedure(
                            "BEGIN register_student(:student_id, :event_id); END;",
                            {"student_id": student_options[selected_student], "event_id": event_options[selected_event]},
                        )
                        st.success("Registration completed. Oracle created or reactivated the registration and ticket.")
                        st.balloons()
                    except Exception as exc:
                        show_error("Registration failed", exc)
    except Exception as exc:
        show_error("Unable to load registration form", exc)

# ---------------- MY TICKETS ----------------
elif page == "🎟️ My Tickets":
    st.header("🎟️ Student Tickets")
    try:
        students = run_query("SELECT student_id, student_name, email FROM students ORDER BY student_name")
        if students.empty:
            st.info("No students found.")
        else:
            student_options = {
                f"{r.STUDENT_NAME} ({int(r.STUDENT_ID)})": int(r.STUDENT_ID)
                for r in students.itertuples()
            }
            selected_student = st.selectbox("Select student", list(student_options))
            sid = student_options[selected_student]
            tickets = run_query("""
                SELECT registration_id, student_id, student_name, email, event_id, event_name,
                       event_date, ticket_id, seat_no, ticket_price, reg_status, payment_status
                FROM student_registrations_view
                WHERE student_id = :student_id
                ORDER BY event_date DESC, registration_id DESC
            """, {"student_id": sid})
            st.dataframe(tickets, use_container_width=True, hide_index=True)
            csv_download(tickets, f"student_{sid}_tickets.csv")
            if tickets.empty:
                st.info("This student has no tickets yet.")
            else:
                active = tickets[tickets["REG_STATUS"] == "CONFIRMED"]
                payable = active[active["PAYMENT_STATUS"] == "PENDING"]
                if not payable.empty:
                    st.subheader("Pending payment")
                    opts = {
                        f"Ticket {int(r.TICKET_ID)} | {r.EVENT_NAME} | ₹{float(r.TICKET_PRICE):.2f}": int(r.TICKET_ID)
                        for r in payable.itertuples()
                    }
                    selected = st.selectbox("Choose a pending ticket", list(opts), key="student_pending_ticket")
                    if st.button("💳 Pay selected ticket", type="primary"):
                        try:
                            email_ok, email_result = pay_ticket_and_email(opts[selected])
                            st.success("Payment status was recorded in Oracle.")
                            if email_ok:
                                st.success(f"📧 Confirmation email sent to {email_result}")
                            else:
                                st.warning("Payment was recorded, but the email could not be sent. Check SMTP configuration.")
                                with st.expander("Email troubleshooting"):
                                    st.code(email_result)
                            st.rerun()
                        except Exception as exc:
                            show_error("Payment failed", exc)
                with st.expander("Cancellation"):
                    cancelable = active.copy()
                    if cancelable.empty:
                        st.info("No confirmed registrations to cancel.")
                    else:
                        reg_options = {
                            f"Registration {int(r.REGISTRATION_ID)} | {r.EVENT_NAME} | {r.REG_STATUS}": int(r.REGISTRATION_ID)
                            for r in cancelable.itertuples()
                        }
                        chosen_reg = st.selectbox("Select registration to cancel", list(reg_options), key="cancel_reg_id")
                        confirm_cancel = st.checkbox("I understand cancellation may release the seat and mark paid tickets REFUNDED.")
                        if st.button("❌ Cancel registration", disabled=not confirm_cancel):
                            try:
                                run_procedure("BEGIN cancel_registration(:registration_id); END;", {"registration_id": reg_options[chosen_reg]})
                                st.success("Registration cancelled. Oracle applied the cancellation and seat-release rules.")
                                st.rerun()
                            except Exception as exc:
                                show_error("Cancellation failed", exc)
    except Exception as exc:
        show_error("Unable to load tickets", exc)

# ---------------- PAY TICKET ----------------
elif page == "💳 Pay Ticket":
    st.header("💳 Pay Ticket")
    st.caption("QR initiation is available. This app does not verify bank transactions or charge real money.")
    try:
        payment_summary = run_query("""
            SELECT t.payment_status, COUNT(*) AS ticket_count,
                   NVL(SUM(t.ticket_price), 0) AS total_amount
            FROM tickets t
            JOIN registrations r ON r.registration_id = t.registration_id
            GROUP BY t.payment_status
            ORDER BY t.payment_status
        """)
        summary_map = {str(r.PAYMENT_STATUS).upper(): int(r.TICKET_COUNT) for r in payment_summary.itertuples()} if not payment_summary.empty else {}
        pc1, pc2, pc3 = st.columns(3)
        pc1.metric("Pending", summary_map.get("PENDING", 0))
        pc2.metric("Paid", summary_map.get("PAID", 0))
        pc3.metric("Refunded", summary_map.get("REFUNDED", 0))

        pending = run_query("""
            SELECT ticket_id, registration_id, student_id, student_name, email, event_id, event_name,
                   seat_no, ticket_price, payment_status, reg_status
            FROM student_registrations_view
            WHERE UPPER(reg_status) = 'CONFIRMED' AND UPPER(payment_status) = 'PENDING'
            ORDER BY ticket_id
        """)
        if pending.empty:
            st.success("There are no confirmed tickets awaiting payment.")
            recent = run_query("""
                SELECT ticket_id, student_name, event_name, ticket_price, payment_status, reg_status
                FROM student_registrations_view
                ORDER BY ticket_id DESC
            """)
            if not recent.empty:
                st.subheader("Recent ticket statuses")
                st.dataframe(recent.head(10), use_container_width=True, hide_index=True)
                csv_download(recent, "ticket_statuses.csv")
        else:
            options = {
                f"Ticket {int(r.TICKET_ID)} | {r.STUDENT_NAME} | {r.EVENT_NAME} | ₹{float(r.TICKET_PRICE):.2f}": int(r.TICKET_ID)
                for r in pending.itertuples()
            }
            selected_label = st.selectbox("Pending ticket", list(options), key="pay_ticket_select")
            selected_ticket = options[selected_label]
            chosen = pending[pending["TICKET_ID"] == selected_ticket].iloc[0]
            amount = float(chosen["TICKET_PRICE"])
            st.write(f"**Student:** {chosen['STUDENT_NAME']}")
            st.write(f"**Event:** {chosen['EVENT_NAME']}")
            st.write(f"**Receipt email:** {chosen['EMAIL'] or 'No email recorded'}")
            st.metric("Amount due", f"₹{amount:,.2f}")

            payment_method = st.radio(
                "Choose payment method",
                ["UPI QR Code", "Demo confirmation (no QR)"],
                horizontal=True,
                key="payment_method_choice",
            )
            if payment_method == "UPI QR Code":
                qr_bytes, qr_label = make_payment_qr(
                    selected_ticket, chosen["EVENT_NAME"], amount, merchant_upi_id
                )
                left, right = st.columns([1, 2])
                with left:
                    st.image(qr_bytes, caption=qr_label, width=260)
                    st.download_button(
                        "⬇️ Download QR",
                        data=qr_bytes,
                        file_name=f"campus_ticket_{selected_ticket}_qr.png",
                        mime="image/png",
                    )
                with right:
                    st.markdown("#### Payment instructions")
                    if merchant_upi_id and "@" in merchant_upi_id:
                        st.write("1. Scan this QR with a UPI app.")
                        st.write(f"2. Check that the amount is ₹{amount:,.2f} and the payee is correct.")
                        st.write("3. Complete payment in your UPI app.")
                        st.warning("The app cannot automatically confirm payment. Do not mark the ticket paid until you verify the transaction separately.")
                    else:
                        st.warning("No valid merchant UPI ID is configured. This is a demo QR only and cannot collect money.")
                        st.write("Enter your merchant UPI ID in the sidebar to generate a UPI payment-intent QR.")

            st.divider()
            st.markdown("#### Demo database confirmation")
            st.caption("For classroom demonstration only. This calls the existing Oracle PAY_TICKET procedure and does not prove that money was received.")
            confirm_pay = st.checkbox(
                "I understand this only updates the demo database; it does not verify a real payment.",
                key="confirm_demo_payment",
            )
            if st.button("✅ Confirm demo payment in Oracle", type="primary", disabled=not confirm_pay):
                try:
                    email_ok, email_result = pay_ticket_and_email(selected_ticket)
                    st.success("Oracle payment status was updated using PAY_TICKET.")
                    if email_ok:
                        st.success(f"📧 Confirmation email sent to {email_result}.")
                    else:
                        st.warning("Database status was updated, but the email could not be sent.")
                        with st.expander("Email troubleshooting details"):
                            st.code(email_result)
                    st.rerun()
                except Exception as exc:
                    show_error("Payment update failed", exc)
    except Exception as exc:
        show_error("Unable to load pending tickets", exc)

# ---------------- REPORTS ----------------
elif page == "📊 Reports":
    st.header("📊 Reports & Analytics")
    tab1, tab2, tab3, tab4 = st.tabs(["💰 Event Revenue", "🏆 Club Performance", "🏟️ Venue Utilization", "📌 Payment Status"])
    try:
        with tab1:
            df = run_query("""
                SELECT event_id, event_name, club_name, registrations, revenue, pending_amount
                FROM v_revenue_per_event
                ORDER BY revenue DESC, registrations DESC
            """)
            st.dataframe(df, use_container_width=True, hide_index=True)
            if not df.empty:
                st.bar_chart(df.set_index("EVENT_NAME")[["REVENUE", "PENDING_AMOUNT"]])
            csv_download(df, "event_revenue_report.csv")
        with tab2:
            df = run_query("""
                SELECT club_id, club_name, total_events, total_registrations, total_revenue
                FROM v_events_per_club
                ORDER BY total_revenue DESC, total_events DESC
            """)
            st.dataframe(df, use_container_width=True, hide_index=True)
            if not df.empty:
                st.bar_chart(df.set_index("CLUB_NAME")[["TOTAL_REGISTRATIONS", "TOTAL_REVENUE"]])
            csv_download(df, "club_performance_report.csv")
        with tab3:
            df = run_query("""
                SELECT venue_id, venue_name, capacity, events_hosted, seats_offered, seats_booked, utilization_pct
                FROM v_venue_utilization
                ORDER BY utilization_pct DESC
            """)
            st.dataframe(df, use_container_width=True, hide_index=True)
            if not df.empty:
                st.bar_chart(df.set_index("VENUE_NAME")[["UTILIZATION_PCT"]])
            csv_download(df, "venue_utilization_report.csv")
        with tab4:
            df = run_query("""
                SELECT payment_status, COUNT(*) AS ticket_count,
                       NVL(SUM(ticket_price),0) AS total_amount
                FROM tickets
                GROUP BY payment_status
                ORDER BY payment_status
            """)
            st.dataframe(df, use_container_width=True, hide_index=True)
            if not df.empty:
                st.bar_chart(df.set_index("PAYMENT_STATUS")[["TICKET_COUNT", "TOTAL_AMOUNT"]])
            csv_download(df, "payment_status_report.csv")
    except Exception as exc:
        show_error("Unable to load one or more reports. Confirm the Phase 1 views exist", exc)

# ---------------- AUDIT LOG ----------------
elif page == "🧾 Audit Log (Phase 2)":
    st.header("🧾 Database Audit Log")
    st.caption("This page works after you run phase2_audit.sql as the CAMPUS schema owner.")
    try:
        df = run_query("""
            SELECT audit_id, table_name, operation_type, record_key,
                   old_value, new_value, changed_by, changed_at
            FROM campus_audit_log
            ORDER BY changed_at DESC
        """)
        if df.empty:
            st.subheader("📊 Audit Overview")
            col1, col2 = st.columns(2)
            col1.metric("Total Changes", 0)
            col2.metric("Latest Activity", "No activity yet")

            st.success("✅ Audit logging is enabled")
            st.markdown("""
            ### No activity recorded yet

            Your audit system is installed and ready. When registration or
            ticket records change, the audit log will display those changes here.

            **Try these actions:**
            - Register a student for an event.
            - Cancel a registration or change its status.
            - Update a ticket's payment status.

            **Information tracked:**
            - Table and operation type
            - Record ID
            - Previous and new values
            - Database user who performed the operation
            - Date and time of the change
            """)
            if st.button("🔄 Refresh Audit Log", use_container_width=False):
                st.rerun()
        else:
            st.subheader("📊 Audit Overview")
            total_changes = len(df)
            latest_activity = str(df.iloc[0]["CHANGED_AT"])[:19] if "CHANGED_AT" in df.columns else "Available"
            col1, col2 = st.columns(2)
            col1.metric("Total Changes", f"{total_changes:,}")
            col2.metric("Latest Activity", latest_activity)

            f1, f2 = st.columns(2)
            with f1:
                table_filter = st.selectbox("Table", ["All"] + sorted(df["TABLE_NAME"].dropna().unique().tolist()))
            with f2:
                operation_filter = st.selectbox("Operation", ["All"] + sorted(df["OPERATION_TYPE"].dropna().unique().tolist()))
            if table_filter != "All":
                df = df[df["TABLE_NAME"] == table_filter]
            if operation_filter != "All":
                df = df[df["OPERATION_TYPE"] == operation_filter]
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv_download(df, "campus_audit_log.csv")
    except Exception as exc:
        st.warning("Audit log is not installed yet. Run `phase2_audit.sql` in SQL Developer as CAMPUS, then reload this page.")
        with st.expander("Technical details"):
            st.code(str(exc))

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh data", use_container_width=True):
    st.rerun()
st.sidebar.caption("Phase 2 build • Existing Phase 1 procedures and views reused")
