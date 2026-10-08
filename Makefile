# astrogallery: make site | preview (local) | deploy (boris builds the live site itself)
SCOPESSD ?= /Volumes/scopessd
PORT     ?= 8000
# boris (Unraid). Override on the command line, e.g. make deploy BORIS=root@boris.local
BORIS    ?= root@192.168.1.3
APPDATA  ?= /mnt/user/appdata/astrogallery
# share on boris laid out like scopessd: Finished/, Gallery/picks.yaml, Gallery/inventory.json
GALLERY_ROOT ?= /mnt/user/Telescopes

.PHONY: all site preview deploy clean

all: deploy

site:
	python3 gallery.py --src "$(SCOPESSD)"

preview: site
	@echo "http://localhost:$(PORT)/"
	python3 -m http.server $(PORT) --bind 127.0.0.1 -d site

deploy:
	BORIS="$(BORIS)" APPDATA="$(APPDATA)" GALLERY_ROOT="$(GALLERY_ROOT)" ./deploy/deploy.sh

clean:
	rm -rf site
