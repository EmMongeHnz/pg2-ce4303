# AstreaView: one target per step of the full run, from the capture to the hologram.
#
#   make help                              # list the targets with their variables
#   make capture SCAN=figura               # host: start the capture (the turntable is turned by hand)
#   make worker NAME=host                  # host, laptop B and Pi: one worker per machine
#   make reconstruct SCAN=figura           # host: distribute the views over the connected workers
#   make host-service SCAN=figura          # host: camera state service (copies the model first when SCAN is given)
#   make viewer                            # host: viewer window driven by the service
#   make agent                             # Pi: device agent (buttons, camera state and models)
#   make hologram                          # Pi: hologram renderer
#
# Every step runs in the foreground, so each one needs its own terminal. Variables can be given on the command line
# (`make reconstruct SCAN=figura WORKERS=3 VOXEL_MM=1.3`) or exported in the environment.

CONFIG ?= config.toml
SCANS_DIR ?= out/scans
MODELS_DIR ?= out/models
VIEWER_SOCKET ?= /tmp/astrea-viewer.sock
HOLOGRAM_SOCKET ?= /tmp/astrea-hologram.sock
RENDERER_BIN ?= renderer/build/renderer

SCAN ?=
NAME ?=
AGENT_NAME ?= pi
WORKERS ?= 2
REFINE ?= azimuth
VOXEL_MM ?=
JOB_TIMEOUT ?=
WAIT ?=
OUT ?=
MAX_GRID_MIB ?=
CHACHA ?= $(if $(filter pi,$(NAME)),1,)
RENDERER_PLATFORM ?=
MODEL ?=
CAPTURE_ARGS ?=
RECONSTRUCT_ARGS ?=
ROLE ?= worker
LINK_NAME ?= $(NAME)

# Models shown at start: the first one by name, which is also the first one the host service chooses, so both
# viewers begin on the same model without having to know the scan name.
FIRST_MODEL = $(firstword $(sort $(wildcard $(MODELS_DIR)/*.glb)))

require = $(if $(strip $($(1))),,$(error $(1) is required, for example: make $(2) $(1)=value))

.DEFAULT_GOAL := help

.PHONY: help build renderer deps open-ports sync sync-host net-check-serve net-check-connect stream-check capture worker \
	reconstruct models host-service viewer agent hologram monitor test test-san clean

help: ## List the targets
	@echo "AstreaView targets (variables in capitals, defaults in brackets):"
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'
	@echo ""
	@echo "Common variables: SCAN NAME CONFIG [config.toml] MODELS_DIR [out/models] WORKERS [2] REFINE [azimuth] VOXEL_MM"
	@echo "                  JOB_TIMEOUT WAIT OUT MAX_GRID_MIB CHACHA MODEL RENDERER_PLATFORM [DRM on the Pi]"

build: ## Build the C parts: libholo and the secure network (every machine)
	$(MAKE) -C libholo
	$(MAKE) -C net

renderer: ## Build the renderer (RENDERER_PLATFORM=DRM on the Pi)
	cmake -S renderer -B renderer/build -DCMAKE_BUILD_TYPE=Release $(if $(RENDERER_PLATFORM),-DRENDERER_PLATFORM=$(RENDERER_PLATFORM))
	cmake --build renderer/build -j

deps: ## Install the build dependencies with apt (needs sudo)
	sudo apt install -y build-essential libssl-dev

open-ports: ## Open the hub ports 7400 and 7401 in the firewall (host, needs sudo)
	sudo ufw allow 7400/tcp
	sudo ufw allow 7401/tcp

sync: ## Install the Python dependencies (laptop B and Pi)
	uv sync

sync-host: ## Install the Python dependencies with capture and reconstruction (host)
	uv sync --extra record3d --extra reconstruction

net-check-serve: ## Host: check the secure links of the three nodes for two minutes
	uv run astrea-net-check --config $(CONFIG) serve --expect worker-b pi:device-agent pi:worker --duration 120

net-check-connect: ## Node: check its link (NAME, ROLE=worker|device-agent)
	$(call require,NAME,net-check-connect)
	uv run astrea-net-check --config $(CONFIG) connect --name $(NAME) --role $(ROLE) $(if $(CHACHA),--prefer-chacha20)

stream-check: ## Host: live view of the iPhone stream with the ArUco pose (q to quit)
	uv run astrea-stream-viewer --ring

capture: ## Host: start the capture of SCAN (turn the plate by hand, q to finish)
	$(call require,SCAN,capture)
	uv run astrea-capture --name $(SCAN) $(CAPTURE_ARGS)

worker: ## Worker NAME (host, worker-b or pi; pi offers ChaCha20 first)
	$(call require,NAME,worker)
	uv run astrea-worker --config $(CONFIG) --name $(NAME) --verbose $(if $(CHACHA),--prefer-chacha20) $(if $(MAX_GRID_MIB),--max-grid-mib $(MAX_GRID_MIB))

reconstruct: ## Host: distributed reconstruction of SCAN (WORKERS, REFINE, VOXEL_MM, JOB_TIMEOUT, WAIT, OUT)
	$(call require,SCAN,reconstruct)
	@test -d $(SCANS_DIR)/$(SCAN) || { echo "there is no scan $(SCANS_DIR)/$(SCAN); run: make capture SCAN=$(SCAN)"; exit 1; }
	uv run astrea-coordinator --config $(CONFIG) --verbose reconstruct $(SCANS_DIR)/$(SCAN) --min-workers $(WORKERS) \
		$(if $(REFINE),--refine-poses $(REFINE)) $(if $(VOXEL_MM),--voxel-mm $(VOXEL_MM)) $(if $(JOB_TIMEOUT),--job-timeout $(JOB_TIMEOUT)) \
		$(if $(WAIT),--wait $(WAIT)) $(if $(OUT),--out $(OUT)) $(RECONSTRUCT_ARGS)

models: ## Host: put the low-detail model of SCAN in the models folder
	$(call require,SCAN,models)
	@test -f $(SCANS_DIR)/$(SCAN)/model/lod-low.glb || { echo "$(SCANS_DIR)/$(SCAN)/model/lod-low.glb does not exist; run: make reconstruct SCAN=$(SCAN)"; exit 1; }
	mkdir -p $(MODELS_DIR)
	cp $(SCANS_DIR)/$(SCAN)/model/lod-low.glb $(MODELS_DIR)/$(SCAN).glb

host-service: $(if $(SCAN),models) ## Host: camera state service; pushes the models to the Pi (SCAN adds that scan first)
	uv run astrea-host --config $(CONFIG) --models-dir $(MODELS_DIR) --renderer-socket $(VIEWER_SOCKET) --verbose

viewer: $(RENDERER_BIN) ## Host: viewer window driven by the service (MODEL, default the first model)
	@test -n "$(if $(MODEL),$(MODEL),$(FIRST_MODEL))" || { echo "there are no models in $(MODELS_DIR); run: make models SCAN=name"; exit 1; }
	$(RENDERER_BIN) --mode viewer --up-axis z --model $(if $(MODEL),$(MODEL),$(FIRST_MODEL)) --control-socket $(VIEWER_SOCKET) --models-dir $(MODELS_DIR)

agent: ## Pi: device agent (needs the holo-io module loaded and the service running on the host)
	uv run astrea-device-agent --config $(CONFIG) --name $(AGENT_NAME) --models-dir $(MODELS_DIR) --renderer-socket $(HOLOGRAM_SOCKET) --verbose

hologram: $(RENDERER_BIN) ## Pi: hologram renderer; start it after the agent has installed a model (MODEL to choose one)
	@test -n "$(if $(MODEL),$(MODEL),$(FIRST_MODEL))" || { echo "there are no models in $(MODELS_DIR) yet; start the agent (make agent) and wait for the host to push one"; exit 1; }
	$(RENDERER_BIN) --mode hologram --up-axis z --model $(if $(MODEL),$(MODEL),$(FIRST_MODEL)) --control-socket $(HOLOGRAM_SOCKET) --models-dir $(MODELS_DIR)

$(RENDERER_BIN):
	$(MAKE) renderer

monitor: ## System monitor of this node (htop)
	htop

test: ## Run the unit tests
	uv run pytest -q

test-san: ## Build the C parts with the memory sanitizers and run the tests against them
	$(MAKE) -C libholo SAN=1
	$(MAKE) -C net SAN=1
	ASTREA_NETD=$(CURDIR)/net/build-san/astrea-netd ASTREA_DEVICE_AGENT=$(CURDIR)/net/build-san/astrea-device-agent uv run pytest -q

clean: ## Remove the C build directories
	$(MAKE) -C libholo clean
	$(MAKE) -C net clean
