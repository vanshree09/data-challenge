{{
    config(
        materialized='table',
        alias='fact_egm_venue_revenue'
    )
}}

SELECT
    venue_code, 
    egm_description,  
    SUM(gmp_sum) AS total_revenue
FROM {{ ref('stg_game_performance') }}
GROUP BY venue_code, egm_description