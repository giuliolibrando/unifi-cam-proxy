#!/bin/bash
# Script per generare go2rtc.yaml dalle variabili d'ambiente

source .env

sed "s/TAPO_USERNAME/${TAPO_USERNAME}/g; s/TAPO_PASSWORD/${TAPO_PASSWORD}/g" \
    go2rtc.yaml.template > go2rtc.yaml

echo "Configurazione go2rtc.yaml generata"

