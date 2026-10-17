"""Read by gunicorn from its working directory (web/, see deploy/slicks-erp.service). Only what the command line does not say.

control_socket_disable (2026-10-17): gunicorn 26 opens a control socket by default, in /run/user/<uid> or the home folder; the service has neither
(ProtectHome=true, no login session), so every start logged "Control server error ... Permission denied". The ERP never uses that socket."""
control_socket_disable = True
