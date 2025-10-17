#!/bin/sh
#
# Get all *_FILE environment variables referencing secrets and export their content as environment variables.
#
# Source: https://stackoverflow.com/questions/39529648/how-to-iterate-through-all-the-env-variables-printing-key-and-value
while IFS='=' read -r -d '' KEY VAL; do
    if [[ ${KEY: -5:5} == "_FILE" ]]; then
        if [[ $VAL == /run/secrets/* ]]; then
            if [ -f $VAL ]; then  # check value is existing path
                # Source https://dev.to/a1ex/tricks-of-declaring-dynamic-variables-in-bash-15b9
                declare -gx "${KEY: 0:-5}"="$(cat $VAL)"
            fi
        fi
    fi
done < <(env -0)

# Execute the original ENTRYPOINT/CMD
exec "$@"
