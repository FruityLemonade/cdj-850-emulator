# SPDX-License-Identifier: GPL-2.0-or-later
# Host check of the decoder (../payload/decoder.c + dr_flac). The SH-2A side is
# built with the shared payload: python3 mods/payloadbuild.py out.bin flac
HOST_CC ?= cc

.PHONY: all test
all: test

build:
	mkdir -p $@

build/host-test: test.c ../payload/decoder.c ../payload/decoder.h ../payload/vendor/dr_flac.h | build
	$(HOST_CC) -std=c99 -O2 -Wall -Wextra -Werror -I../payload test.c ../payload/decoder.c -o $@

test: build/host-test
	python3 test_decoder.py build/host-test
