from django.urls import path
from sfb_api.api import views, views_catalog

urlpatterns = [
    # Health check
    path('', views.health_check, name='health'),

    # Fitment catalog. Vehicle dropdowns are public. Every other route uses X-SFB-Key.
    path('api/vehicles/', views_catalog.vehicle_list, name='vehicle-list'),
    path('api/vehicles/search', views_catalog.vehicle_search, name='vehicle-search'),
    path('api/vehicles/search/', views_catalog.vehicle_search, name='vehicle-search-slash'),
    path('api/parts/', views_catalog.parts_for_vehicle, name='parts-for-vehicle'),
    path('api/parts/search', views_catalog.part_search, name='part-search'),
    path('api/parts/search/', views_catalog.part_search, name='part-search-slash'),
    path('api/parts/sources/', views_catalog.listing_sources, name='listing-sources'),
    path('api/parts/find/', views_catalog.listing_find, name='listing-find'),
    path('api/fitment/', views_catalog.fitment_for_part, name='fitment-for-part'),
    path('api/fitment/confirm/', views_catalog.fitment_confirm, name='fitment-confirm'),
    path('api/fitment/cross-car', views_catalog.cross_car, name='fitment-cross-car'),
    path('api/fitment/cross-car/', views_catalog.cross_car, name='fitment-cross-car-slash'),
    path('api/watch/list/', views_catalog.watch_list, name='watch-list'),
    path('api/watch/list', views_catalog.watch_list, name='watch-list-noslash'),
    path('api/watch/<str:watch_id>/results/', views_catalog.watch_results, name='watch-results'),
    path('api/watch/<str:watch_id>/', views_catalog.watch_stop, name='watch-stop'),
    path('api/watch/', views_catalog.watch, name='watch'),
    path('api/watch', views_catalog.watch, name='watch-noslash'),
    path('api/catalog/grow/', views_catalog.catalog_grow, name='catalog-grow'),
    path('api/catalog/import/', views_catalog.catalog_import, name='catalog-import'),
    path('api/growth/run/', views_catalog.growth_run, name='growth-run'),

    # Billing. The webhook is public and checks the Stripe signature.
    path('api/billing/subscription/', views_catalog.billing_subscription, name='billing-subscription'),
    path('api/billing/checkout/', views_catalog.billing_checkout, name='billing-checkout'),
    path('api/billing/portal/', views_catalog.billing_portal, name='billing-portal'),
    path('api/billing/webhook/', views_catalog.stripe_webhook, name='billing-webhook'),

    # Matches
    path('api/matches/', views.match_list, name='match-list'),
    path('api/matches/<str:match_id>/', views.match_detail, name='match-detail'),
    path('api/matches/<str:match_id>/action/', views.match_action, name='match-action'),

    # Sections
    path('api/sections/', views.section_list, name='section-list'),

    # Listings
    path('api/listings/buyers/', views.buyer_list, name='buyer-list'),
    path('api/listings/sellers/', views.seller_list, name='seller-list'),

    # Seeker
    path('api/seeker/markets/', views.seeker_markets, name='seeker-markets'),
    path('api/seeker/markets/<str:market_id>/', views.seeker_market_detail, name='seeker-market-detail'),
    path('api/seeker/status/', views.seeker_status, name='seeker-status'),
    path('api/seeker/run/', views.seeker_run, name='seeker-run'),
    path('api/seeker/runs/<str:run_id>/', views.seeker_run_detail, name='seeker-run-detail'),

    # Live matcher
    path('api/match/analyze/', views.match_analyze, name='match-analyze'),
]
