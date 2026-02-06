# Meeting Notes Processor — deploy to nuctu
#
# The daemon (meetingnotesd) runs on nuctu as a systemd user service.
# Deploy by pushing to git, pulling on nuctu, and restarting the service.
#
# Usage: make deploy

REMOTE_HOST := edd@nuctu
REMOTE_DIR := ~/git/meeting-notes-processor
SERVICE := meetingnotes-webhook

.PHONY: deploy status logs restart ssh push

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

deploy: push  ## Push to git and deploy to nuctu
	@echo "=== Deploying to nuctu ==="
	@ssh $(REMOTE_HOST) '\
		cd $(REMOTE_DIR) && \
		git pull --ff-only && \
		systemctl --user restart $(SERVICE) && \
		sleep 2 && \
		systemctl --user is-active $(SERVICE) && \
		echo "=== Deploy complete ===" \
	'

push:  ## Push local changes to remote
	@git push

# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------

restart:  ## Restart the service on nuctu
	@ssh $(REMOTE_HOST) 'systemctl --user restart $(SERVICE)'
	@echo "Service restarted"

status:  ## Show service status on nuctu
	@ssh $(REMOTE_HOST) 'systemctl --user status $(SERVICE) --no-pager'

logs:  ## Tail service logs on nuctu
	@ssh $(REMOTE_HOST) 'journalctl --user -u $(SERVICE) -f --no-pager'

ssh:  ## SSH to nuctu
	@ssh $(REMOTE_HOST)
