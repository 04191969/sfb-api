from django.urls import path
from sfb_api.api import views

urlpatterns = [
    # Health check
    path('', views.health_check, name='health'),

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
    path('api/seeker/status/', views.seeker_status, name='seeker-status'),
    path('api/seeker/run/', views.seeker_run, name='seeker-run'),

    # Live matcher
    path('api/match/analyze/', views.match_analyze, name='match-analyze'),
]
