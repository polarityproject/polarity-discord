# sfvr-discord (rooms_bot.py): one Discord message kept showing SFVR's public
# rooms from the rooms API. Built by compose.yaml.
FROM python:3.12.7-alpine3.20
COPY rooms_bot.py /usr/local/bin/rooms_bot.py
# /data holds the message's id and pending notices (state.json) and the
# health file; a fresh named volume takes this owner.
RUN install -d -o 65534 -g 65534 -m 0700 /data
USER 65534:65534
VOLUME /data
ENTRYPOINT ["python3", "-u", "/usr/local/bin/rooms_bot.py"]
