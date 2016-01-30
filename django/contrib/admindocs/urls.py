# encoding: utf-8
from django.conf.urls import url
from django.contrib.admindocs import views

urlpatterns = [
    url('^$',
        views.BaseAdminDocsView.as_view(template_name='admin_doc/index.html'),
        name='django-admindocs-docroot'),
    url('^bookmarklets/$',
        views.BookmarkletsView.as_view(),
        name='django-admindocs-bookmarklets'),
    url('^tags/$',
        views.TemplateTagIndexView.as_view(),
        name='django-admindocs-tags'),
    url('^filters/$',
        views.TemplateFilterIndexView.as_view(),
        name='django-admindocs-filters'),
    url('^views/$',
        views.ViewIndexView.as_view(),
        name='django-admindocs-views-index'),
    url('^views/(?P<view>[^/]+)/$',
        views.ViewDetailView.as_view(),
        name='django-admindocs-views-detail'),
    url('^models/$',
        views.ModelIndexView.as_view(),
        name='django-admindocs-models-index'),
    url('^models/(?P<app_label>[^\.]+)\.(?P<model_name>[^/]+)/$',
        views.ModelDetailView.as_view(),
        name='django-admindocs-models-detail'),
    url('^templates/(?P<template>.*)/$',
        views.TemplateDetailView.as_view(),
        name='django-admindocs-templates'),
]
