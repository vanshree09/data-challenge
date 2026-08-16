{{
    config(
        materialized='incremental',
        alias='game_performance'
    )
}}

SELECT
    CAST(bus_date AS date) AS bus_date,
    CAST(venue_code AS integer) AS venue_code,
    egm_description,
    manufacturer,
    CAST(fp AS integer) AS fp,
    CAST(turnover_sum AS numeric(12,2)) AS turnover_sum,
    CAST(gmp_sum AS numeric(12,2)) AS gmp_sum,
    CAST(games_played_sum AS bigint) AS games_played_sum    
FROM {{ source('raw', 'game_performance') }}

{% if is_incremental() %}
WHERE CAST(bus_date AS date) > (SELECT max(bus_date) FROM {{ this }})
{% endif %}