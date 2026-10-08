# astrogallery: make site | preview (local) | deploy (boris builds the live site itself)
# the telescopes share (smb://boris/Telescopes) as mounted on this machine
TELESCOPES ?= /Volumes/Telescopes
PORT     ?= 8000
# boris (Unraid). Override on the command line, e.g. make deploy BORIS=root@boris.local
BORIS    ?= root@192.168.1.3
APPDATA  ?= /mnt/user/appdata/astrogallery
# the same share on boris: Finished/, Source Data/, NINA/, Gallery/picks.yaml
GALLERY_ROOT ?= /mnt/user/Telescopes

.PHONY: all site preview deploy clean

all: deploy

site:
	python3 gallery.py --root "$(TELESCOPES)"

preview: site
	@echo "http://localhost:$(PORT)/"
	python3 -m http.server $(PORT) --bind 127.0.0.1 -d site

deploy:
	BORIS="$(BORIS)" APPDATA="$(APPDATA)" GALLERY_ROOT="$(GALLERY_ROOT)" ./deploy/deploy.sh

clean:
	rm -rf site
