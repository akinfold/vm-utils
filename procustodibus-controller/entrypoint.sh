#!/bin/sh
#
# Get all *_FILE environment variables referencing secrets and export their content as environment variables.
#
# Source: https://stackoverflow.com/questions/39529648/how-to-iterate-through-all-the-env-variables-printing-key-and-value
# env -0 | while IFS='=' read -r -d '' KEY VAL; do
#     if [[ ${KEY: -5:5} == "_FILE" ]]; then
#         if [[ $VAL == /run/secrets/* ]]; then
#             if [ -f $VAL ]; then  # check value is existing path
#                 # Source https://dev.to/a1ex/tricks-of-declaring-dynamic-variables-in-bash-15b9
#                 declare -gx "${KEY: 0:-5}"="$(cat $VAL)"
#             fi
#         fi
#     fi
# done

export DB_PASSWORD=$(cat /run/secrets/procustodibus_db_user_password)
export DB_ALEK_1=$(cat /run/secrets/procustodibus_db_alek_1)
export SIGNUP_KEY=$(cat /run/secrets/procustodibus_signup_key)

# Execute the original ENTRYPOINT/CMD
exec "$@"
