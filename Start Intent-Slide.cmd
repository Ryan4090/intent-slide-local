@echo off
setlocal DisableDelayedExpansion
rem Transfer directly; CALL would expand percent characters in the clone path again.
"%~dp0intent-slide.cmd" start --open %*
