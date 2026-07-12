FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build
# traçabilité des mises à jour : build.json servi en no-cache (nginx.conf),
# affiché dans Paramètres → Système à côté du build de l'API
ARG GIT_SHA=dev
ARG BUILD_TIME=
RUN printf '{"sha":"%s","time":"%s"}' "$GIT_SHA" "$BUILD_TIME" > dist/build.json

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
