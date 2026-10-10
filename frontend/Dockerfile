FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci

FROM dependencies AS development
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM dependencies AS build
ARG VITE_MAP_STYLE_URL=https://tiles.openfreemap.org/styles/positron
ENV VITE_MAP_STYLE_URL=$VITE_MAP_STYLE_URL
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS production
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# Match nginx's IPv4 listener rather than BusyBox's IPv6 localhost resolution.
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1

