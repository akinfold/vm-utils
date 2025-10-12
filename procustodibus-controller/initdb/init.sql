-- run as super-user to create new user and db
CREATE USER procustodibus_user WITH PASSWORD '{{ PROCUSTODIBUS_USER_PASSWORD }}';
CREATE DATABASE procustodibus_db OWNER procustodibus_user;
