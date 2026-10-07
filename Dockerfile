# Serves the static site. Build the data first (see README: python3 -m pipeline.export),
# then: docker build -t pulp-explorer . && docker run -p 8080:80 pulp-explorer
FROM nginx:1.27-alpine
COPY site/ /usr/share/nginx/html/
RUN rm -f /usr/share/nginx/html/_*.html && \
    printf 'gzip on;\ngzip_types application/json application/javascript text/css;\ngzip_min_length 1024;\n' > /etc/nginx/conf.d/gzip.conf
