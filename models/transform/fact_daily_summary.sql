{{
    config(
        materialized='incremental',
        unique_key=['bus_date'],
        alias='fact_daily_summary'
    )
}}

SELECT
    bus_date, 
    SUM(turnover_sum) AS total_turnover, 
    SUM(gmp_sum) AS total_revenue   
FROM {{ ref('stg_game_performance') }}

{% if is_incremental() %}
WHERE bus_date > (SELECT max(bus_date) FROM {{ this }})
{% endif %}

GROUP BY bus_date