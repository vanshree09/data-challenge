{{
    config(
        materialized='table',
        alias='fact_venue_turnover'
    )
}}

SELECT
    venue_code,  
    sum(turnover_sum) as total_turnover
FROM {{ ref('stg_game_performance') }}
GROUP BY venue_code