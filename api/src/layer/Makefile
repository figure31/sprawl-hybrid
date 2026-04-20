.PHONY: build-SharedLayer

# SAM invokes this target via `BuildMethod: makefile`.
# Force Linux arm64 wheels since Lambda runs on Graviton and we're
# building on a Mac. Without this, native modules like PyCryptodome get
# installed with the host's architecture and fail at runtime.
build-SharedLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	pip install -r requirements.txt \
		-t "$(ARTIFACTS_DIR)/python" \
		--platform manylinux2014_aarch64 \
		--only-binary :all: \
		--python-version 3.12 \
		--implementation cp \
		--abi cp312 \
		--upgrade
	cp -r python/sprawl_common "$(ARTIFACTS_DIR)/python/sprawl_common"
