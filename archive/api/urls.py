from django.urls import path

from . import views

urlpatterns = [
    path('', views.api_v1_root, name='api_v1_root'),
    path('site/', views.api_v1_site, name='api_v1_site'),
    path('home/', views.api_v1_home, name='api_v1_home'),
    path('pages/', views.api_v1_pages, name='api_v1_pages'),
    path('pages/<slug:slug>/', views.api_v1_page_detail, name='api_v1_page_detail'),
    path('documents/', views.api_v1_documents, name='api_v1_documents'),
    # Must stay ahead of the <slug> route below, which would otherwise swallow "meta".
    path('documents/meta/', views.api_v1_documents_meta, name='api_v1_documents_meta'),
    path('documents/<slug:slug>/mirrors/', views.api_v1_document_mirrors, name='api_v1_document_mirrors'),
    path('documents/<slug:slug>/', views.api_v1_document_detail, name='api_v1_document_detail'),
    path('categories/', views.api_v1_categories, name='api_v1_categories'),
    path('categories/<slug:slug>/', views.api_v1_category_detail, name='api_v1_category_detail'),
    path('schedules/', views.api_v1_schedules, name='api_v1_schedules'),
    path('schedules/conventions/<slug:category_slug>/', views.api_v1_schedule_years, name='api_v1_schedule_years'),
    path('schedules/<slug:slug>/events/', views.api_v1_schedule_events, name='api_v1_schedule_events'),
    path('schedules/<slug:slug>/', views.api_v1_schedule_detail, name='api_v1_schedule_detail'),
    path('search/', views.api_v1_search, name='api_v1_search'),
    path('vault/', views.api_v1_vault, name='api_v1_vault'),
    path('vault/meta/', views.api_v1_vault_meta, name='api_v1_vault_meta'),
    path('vault/<int:item_id>/', views.api_v1_vault_detail, name='api_v1_vault_detail'),
    path('tags/', views.api_v1_tags, name='api_v1_tags'),
    path('tags/<slug:slug>/', views.api_v1_tag_detail, name='api_v1_tag_detail'),
    path('stats/', views.api_v1_stats, name='api_v1_stats'),
    path('telegram/', views.api_v1_telegram, name='api_v1_telegram'),
]
