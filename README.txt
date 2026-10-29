CAMPUS EVENT REGISTRATION & TICKET MANAGEMENT SYSTEM — PHASE 2

Files:
- app.py: Streamlit application (original Phase 2 app with QR initiation added)
- requirements.txt: Python packages
- phase2_audit.sql: Phase 2 audit table and trigger script

Run on Windows:
1. Open Command Prompt in this folder.
2. Install packages:
   py -m pip install -r requirements.txt
3. Run:
   py -m streamlit run app.py
4. In the sidebar, enter your Oracle database credentials and service name.
5. Optional: enter a valid merchant UPI ID to create a UPI payment-intent QR.

Notes:
- QR generation alone does not verify or collect payment.
- Without a merchant UPI ID, the app shows a demo-only QR.
- The Oracle confirmation button is for classroom demonstration and calls the existing PAY_TICKET procedure.
- Real payment processing requires a payment gateway and server-side transaction verification.
- Run phase2_audit.sql as the CAMPUS schema owner if the audit objects are not installed.
