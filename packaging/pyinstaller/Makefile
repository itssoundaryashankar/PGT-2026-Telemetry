REPO_ROOT := $(abspath ../..)
PYINSTALLER := python3 -m PyInstaller

.PHONY: all receiver sender clean

all: receiver sender

receiver:
	cd $(REPO_ROOT) && $(PYINSTALLER) \
		--clean \
		--onefile \
		--name telemetry_receiver \
		--paths "$(REPO_ROOT)" \
		--hidden-import BMV.bmv_handler \
		--hidden-import LORA.lora_transport \
		--hidden-import storage.event_csv_sink \
		--hidden-import telemetry_packet \
		telemetry_receiver.py

sender:
	cd $(REPO_ROOT) && $(PYINSTALLER) \
		--clean \
		--onefile \
		--name telemetry_sender \
		--paths "$(REPO_ROOT)" \
		--hidden-import BMV.bmv_normalizer \
		--hidden-import BMV.bmv_policy \
		--hidden-import BMV.bmv_reader \
		--hidden-import LORA.lora_transport \
		--hidden-import storage.csv_sink \
		--hidden-import telemetry_packet \
		telemetry_sender.py

clean:
	rm -rf $(REPO_ROOT)/build $(REPO_ROOT)/dist
