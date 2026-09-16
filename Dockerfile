# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY zetu_closed_book_writer.py voice_guide.txt ./

# No CMD that auto-runs the pipeline on container start -- this service is
# invoked on demand (manually today; via a Railway Cron Job's own command
# once the schedule is deliberately turned on -- see railway.toml, which
# ships with no cronSchedule set). Idle by default so a deploy never
# triggers an OpenAI/Anthropic call or produces output on its own.
CMD ["tail", "-f", "/dev/null"]
