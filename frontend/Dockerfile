# Static frontend, served by nginx. nginx also reverse-proxies /api/ to the
# hoploy backend — see nginx.conf.template — so the browser only ever talks
# to this one container.
FROM nginx:1.27-alpine

# Processed at container startup by the official nginx image's built-in
# envsubst step (see docker-compose.yml's NGINX_ENVSUBST_FILTER) into
# /etc/nginx/conf.d/default.conf.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

COPY index.html /usr/share/nginx/html/index.html
COPY css/ /usr/share/nginx/html/css/
COPY js/ /usr/share/nginx/html/js/
COPY data/ /usr/share/nginx/html/data/

EXPOSE 80
