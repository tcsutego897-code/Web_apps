FROM php:8.1-apache
WORKDIR /var/www/html
COPY . /var/www/html
RUN apt-get update && apt-get install -y zip unzip git \
  && if [ -f composer.json ]; then \
       curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer && \
       composer install --no-dev --prefer-dist --no-interaction; \
     fi \
  && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN a2enmod rewrite
EXPOSE 80
CMD ["apache2-foreground"]
