#!/bin/bash
# Git sync commands for VidUtils

# Check for remote updates
git fetch origin && git log HEAD..origin/main --oneline

# Pull updates (handles divergent branches with merge strategy)
git pull origin main --no-rebase

# Or combined in one command:
# git fetch origin && git pull origin main --no-rebase