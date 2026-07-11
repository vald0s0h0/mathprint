# Contexte de build : racine du dépôt (voir docker-compose.yml)
FROM node:22-alpine
WORKDIR /srv
# clone MathALÉA épinglé (versionné dans le dépôt) + ses dépendances
COPY mathalea /srv/mathalea
RUN cd /srv/mathalea && npm install --no-audit --no-fund --loglevel=error
COPY mathalea-service /srv/mathalea-service
WORKDIR /srv/mathalea-service
RUN npm install --no-audit --no-fund
ENV MATHALEA_ROOT=/srv/mathalea PORT=8123
EXPOSE 8123
CMD ["node", "--import", "./register.mjs", "server.js"]
