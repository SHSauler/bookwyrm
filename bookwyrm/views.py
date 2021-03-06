''' views for pages you can go to in the application '''
import re

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Avg, Q
from django.http import HttpResponseBadRequest, HttpResponseNotFound,\
        JsonResponse
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.csrf import csrf_exempt

from bookwyrm import outgoing
from bookwyrm.activitypub import ActivityEncoder
from bookwyrm import forms, models, books_manager
from bookwyrm import goodreads_import
from bookwyrm.tasks import app
from bookwyrm.utils import regex


def get_user_from_username(username):
    ''' helper function to resolve a localname or a username to a user '''
    try:
        user = models.User.objects.get(localname=username)
    except models.User.DoesNotExist:
        user = models.User.objects.get(username=username)
    return user


def is_api_request(request):
    ''' check whether a request is asking for html or data '''
    return 'json' in request.headers.get('Accept') or \
            request.path[-5:] == '.json'


def server_error_page(request):
    ''' 500 errors '''
    return TemplateResponse(request, 'error.html', {'title': 'Oops!'})


def not_found_page(request, _):
    ''' 404s '''
    return TemplateResponse(request, 'notfound.html', {'title': 'Not found'})


@login_required
def home(request):
    ''' this is the same as the feed on the home tab '''
    return home_tab(request, 'home')


@login_required
def home_tab(request, tab):
    ''' user's homepage with activity feed '''
    # TODO: why on earth would this be where the pagination is set
    page_size = 15
    try:
        page = int(request.GET.get('page', 1))
    except ValueError:
        page = 1

    max_books = 5
    book_count = 0
    preset_shelves = ['reading', 'read', 'to-read']
    suggested_books = []
    for preset in preset_shelves:
        limit = max_books - book_count
        shelf = request.user.shelf_set.get(identifier=preset)

        shelf_books = shelf.shelfbook_set.order_by(
            '-updated_date'
        ).all()[:limit]
        shelf_preview = {
            'name': shelf.name,
            'books': [s.book for s in shelf_books]
        }
        suggested_books.append(shelf_preview)
        book_count += len(shelf_preview['books'])

    activities = get_activity_feed(request.user, tab)

    activity_count = activities.count()
    activities = activities[(page - 1) * page_size:page * page_size]

    next_page = '/?page=%d#feed' % (page + 1)
    prev_page = '/?page=%d#feed' % (page - 1)
    data = {
        'title': 'Updates Feed',
        'user': request.user,
        'suggested_books': suggested_books,
        'activities': activities,
        'review_form': forms.ReviewForm(),
        'quotation_form': forms.QuotationForm(),
        'tab': tab,
        'comment_form': forms.CommentForm(),
        'next': next_page if activity_count > (page_size * page) else None,
        'prev': prev_page if page > 1 else None,
    }
    return TemplateResponse(request, 'feed.html', data)


def get_activity_feed(user, filter_level, model=models.Status):
    ''' get a filtered queryset of statuses '''
    # status updates for your follow network
    if user.is_anonymous:
        user = None
    if user:
        following = models.User.objects.filter(
            Q(followers=user) | Q(id=user.id)
        )
    else:
        following = []

    activities = model
    if hasattr(model, 'objects'):
        activities = model.objects

    activities = activities.filter(
        deleted=False
    ).order_by(
        '-published_date'
    )

    if hasattr(activities, 'select_subclasses'):
        activities = activities.select_subclasses()

    if filter_level in ['friends', 'home']:
        # people you follow and direct mentions
        activities = activities.filter(
            Q(user__in=following, privacy__in=[
                'public', 'unlisted', 'followers'
            ]) | Q(mention_users=user) | Q(user=user)
        )
    elif filter_level == 'self':
        activities = activities.filter(user=user, privacy='public')
    elif filter_level == 'local':
        # everyone on this instance except unlisted
        activities = activities.filter(
            Q(user__in=following, privacy='followers') | Q(privacy='public'),
            user__local=True
        )
    else:
        # all activities from everyone you federate with
        activities = activities.filter(
            Q(user__in=following, privacy='followers') | Q(privacy='public')
        )

    return activities


def search(request):
    ''' that search bar up top '''
    query = request.GET.get('q')

    if is_api_request(request):
        # only return local book results via json so we don't cause a cascade
        book_results = books_manager.local_search(query)
        return JsonResponse([r.__dict__ for r in book_results], safe=False)

    # use webfinger  looks like a mastodon style account@domain.com username
    if re.match(regex.full_username, query):
        outgoing.handle_remote_webfinger(query)

    # do a local user search
    user_results = models.User.objects.annotate(
        similarity=TrigramSimilarity('username', query),
    ).filter(
        similarity__gt=0.1,
    ).order_by('-similarity')[:10]

    book_results = books_manager.search(query)
    data = {
        'title': 'Search Results',
        'book_results': book_results,
        'user_results': user_results,
        'query': query,
    }
    return TemplateResponse(request, 'search_results.html', data)


@login_required
def import_page(request):
    ''' import history from goodreads '''
    return TemplateResponse(request, 'import.html', {
        'title': 'Import Books',
        'import_form': forms.ImportForm(),
        'jobs': models.ImportJob.
                objects.filter(user=request.user).order_by('-created_date'),
        'limit': goodreads_import.MAX_ENTRIES,
    })


@login_required
def import_status(request, job_id):
    ''' status of an import job '''
    job = models.ImportJob.objects.get(id=job_id)
    if job.user != request.user:
        raise PermissionDenied
    task = app.AsyncResult(job.task_id)
    return TemplateResponse(request, 'import_status.html', {
        'title': 'Import Status',
        'job': job,
        'items': job.items.order_by('index').all(),
        'task': task
    })


def login_page(request):
    ''' authentication '''
    if request.user.is_authenticated:
        return redirect('/')
    # send user to the login page
    data = {
        'title': 'Login',
        'site_settings': models.SiteSettings.get(),
        'login_form': forms.LoginForm(),
        'register_form': forms.RegisterForm(),
    }
    return TemplateResponse(request, 'login.html', data)


def about_page(request):
    ''' more information about the instance '''
    data = {
        'title': 'About',
        'site_settings': models.SiteSettings.get(),
    }
    return TemplateResponse(request, 'about.html', data)


def password_reset_request(request):
    ''' invite management page '''
    return TemplateResponse(
        request,
        'password_reset_request.html',
        {'title': 'Reset Password'}
    )


def password_reset(request, code):
    ''' endpoint for sending invites '''
    if request.user.is_authenticated:
        return redirect('/')
    try:
        reset_code = models.PasswordReset.objects.get(code=code)
        if not reset_code.valid():
            raise PermissionDenied
    except models.PasswordReset.DoesNotExist:
        raise PermissionDenied

    return TemplateResponse(
        request,
        'password_reset.html',
        {'title': 'Reset Password', 'code': reset_code.code}
    )


def invite_page(request, code):
    ''' endpoint for sending invites '''
    if request.user.is_authenticated:
        return redirect('/')
    try:
        invite = models.SiteInvite.objects.get(code=code)
        if not invite.valid():
            raise PermissionDenied
    except models.SiteInvite.DoesNotExist:
        raise PermissionDenied

    data = {
        'title': 'Join',
        'site_settings': models.SiteSettings.get(),
        'register_form': forms.RegisterForm(),
        'invite': invite,
    }
    return TemplateResponse(request, 'invite.html', data)

@login_required
@permission_required('bookwyrm.create_invites', raise_exception=True)
def manage_invites(request):
    ''' invite management page '''
    data = {
        'title': 'Invitations',
        'invites': models.SiteInvite.objects.filter(user=request.user),
        'form': forms.CreateInviteForm(),
    }
    return TemplateResponse(request, 'manage_invites.html', data)


@login_required
def notifications_page(request):
    ''' list notitications '''
    notifications = request.user.notification_set.all() \
            .order_by('-created_date')
    unread = [n.id for n in notifications.filter(read=False)]
    data = {
        'title': 'Notifications',
        'notifications': notifications,
        'unread': unread,
    }
    notifications.update(read=True)
    return TemplateResponse(request, 'notifications.html', data)

@csrf_exempt
def user_page(request, username, subpage=None, shelf=None):
    ''' profile page for a user '''
    try:
        user = get_user_from_username(username)
    except models.User.DoesNotExist:
        return HttpResponseNotFound()

    if is_api_request(request):
        # we have a json request
        return JsonResponse(user.to_activity(), encoder=ActivityEncoder)
    # otherwise we're at a UI view

    data = {
        'title': user.name,
        'user': user,
        'is_self': request.user.id == user.id,
    }
    if subpage == 'followers':
        data['followers'] = user.followers.all()
        return TemplateResponse(request, 'followers.html', data)
    if subpage == 'following':
        data['following'] = user.following.all()
        return TemplateResponse(request, 'following.html', data)
    if subpage == 'shelves':
        data['shelves'] = user.shelf_set.all()
        if shelf:
            data['shelf'] = user.shelf_set.get(identifier=shelf)
        else:
            data['shelf'] = user.shelf_set.first()
        return TemplateResponse(request, 'shelf.html', data)

    data['shelf_count'] = user.shelf_set.count()
    shelves = []
    for user_shelf in user.shelf_set.all():
        if not user_shelf.books.count():
            continue
        shelves.append({
            'name': user_shelf.name,
            'remote_id': user_shelf.remote_id,
            'books': user_shelf.books.all()[:3],
            'size': user_shelf.books.count(),
        })
        if len(shelves) > 2:
            break

    data['shelves'] = shelves
    data['activities'] = get_activity_feed(user, 'self')[:15]
    return TemplateResponse(request, 'user.html', data)


@csrf_exempt
def followers_page(request, username):
    ''' list of followers '''
    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        user = get_user_from_username(username)
    except models.User.DoesNotExist:
        return HttpResponseNotFound()

    if is_api_request(request):
        return JsonResponse(user.to_followers_activity(**request.GET))

    return user_page(request, username, subpage='followers')


@csrf_exempt
def following_page(request, username):
    ''' list of followers '''
    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        user = get_user_from_username(username)
    except models.User.DoesNotExist:
        return HttpResponseNotFound()

    if is_api_request(request):
        return JsonResponse(user.to_following_activity(**request.GET))

    return user_page(request, username, subpage='following')


@csrf_exempt
def user_shelves_page(request, username):
    ''' list of followers '''
    if request.method != 'GET':
        return HttpResponseBadRequest()

    return user_page(request, username, subpage='shelves')


@csrf_exempt
def status_page(request, username, status_id):
    ''' display a particular status (and replies, etc) '''
    if request.method != 'GET':
        return HttpResponseBadRequest()

    try:
        user = get_user_from_username(username)
        status = models.Status.objects.select_subclasses().get(id=status_id)
    except ValueError:
        return HttpResponseNotFound()

    # the url should have the poster's username in it
    if user != status.user:
        return HttpResponseNotFound()

    # make sure the user is authorized to see the status
    if not status_visible_to_user(request.user, status):
        return HttpResponseNotFound()

    if is_api_request(request):
        return JsonResponse(status.to_activity(), encoder=ActivityEncoder)

    data = {
        'title': 'Status by %s' % user.username,
        'status': status,
    }
    return TemplateResponse(request, 'status.html', data)

def status_visible_to_user(viewer, status):
    ''' is a user authorized to view a status? '''
    if viewer == status.user or status.privacy in ['public', 'unlisted']:
        return True
    if status.privacy == 'followers' and \
            status.user.followers.filter(id=viewer.id).first():
        return True
    if status.privacy == 'direct' and \
            status.mention_users.filter(id=viewer.id).first():
        return True
    return False



@csrf_exempt
def replies_page(request, username, status_id):
    ''' ordered collection of replies to a status '''
    if request.method != 'GET':
        return HttpResponseBadRequest()

    if not is_api_request(request):
        return status_page(request, username, status_id)

    status = models.Status.objects.get(id=status_id)
    if status.user.localname != username:
        return HttpResponseNotFound()

    return JsonResponse(
        status.to_replies(**request.GET),
        encoder=ActivityEncoder
    )


@login_required
def edit_profile_page(request):
    ''' profile page for a user '''
    user = request.user

    form = forms.EditUserForm(instance=request.user)
    data = {
        'title': 'Edit profile',
        'form': form,
        'user': user,
    }
    return TemplateResponse(request, 'edit_user.html', data)


def book_page(request, book_id):
    ''' info about a book '''
    book = models.Book.objects.select_subclasses().get(id=book_id)
    if is_api_request(request):
        return JsonResponse(book.to_activity(), encoder=ActivityEncoder)

    if isinstance(book, models.Work):
        book = book.default_edition
    if not book:
        return HttpResponseNotFound()

    work = book.parent_work
    if not work:
        return HttpResponseNotFound()

    reviews = models.Review.objects.filter(
        book__in=work.edition_set.all(),
    )
    reviews = get_activity_feed(request.user, 'federated', model=reviews)

    user_tags = []
    readthroughs = []
    if request.user.is_authenticated:
        user_tags = models.Tag.objects.filter(
            book=book, user=request.user
        ).values_list('identifier', flat=True)

        readthroughs = models.ReadThrough.objects.filter(
            user=request.user,
            book=book,
        ).order_by('start_date')

    rating = reviews.aggregate(Avg('rating'))
    tags = models.Tag.objects.filter(
        book=book
    ).values(
        'book', 'name', 'identifier'
    ).distinct().all()

    data = {
        'title': book.title,
        'book': book,
        'reviews': reviews.filter(content__isnull=False),
        'ratings': reviews.filter(content__isnull=True),
        'rating': rating['rating__avg'],
        'tags': tags,
        'user_tags': user_tags,
        'review_form': forms.ReviewForm(),
        'quotation_form': forms.QuotationForm(),
        'comment_form': forms.CommentForm(),
        'readthroughs': readthroughs,
        'tag_form': forms.TagForm(),
        'path': '/book/%s' % book_id,
        'cover_form': forms.CoverForm(instance=book),
        'info_fields': [
            {'name': 'ISBN', 'value': book.isbn_13},
            {'name': 'OCLC number', 'value': book.oclc_number},
            {'name': 'OpenLibrary ID', 'value': book.openlibrary_key},
            {'name': 'Goodreads ID', 'value': book.goodreads_key},
            {'name': 'Format', 'value': book.physical_format},
            {'name': 'Pages', 'value': book.pages},
        ],
    }
    return TemplateResponse(request, 'book.html', data)


@login_required
@permission_required('bookwyrm.edit_book', raise_exception=True)
def edit_book_page(request, book_id):
    ''' info about a book '''
    book = books_manager.get_edition(book_id)
    if not book.description:
        book.description = book.parent_work.description
    data = {
        'title': 'Edit Book',
        'book': book,
        'form': forms.EditionForm(instance=book)
    }
    return TemplateResponse(request, 'edit_book.html', data)


def editions_page(request, book_id):
    ''' list of editions of a book '''
    try:
        work = models.Work.objects.get(id=book_id)
    except models.Work.DoesNotExist:
        return HttpResponseNotFound()

    if is_api_request(request):
        return JsonResponse(
            work.to_edition_list(**request.GET),
            encoder=ActivityEncoder
        )

    editions = models.Edition.objects.filter(parent_work=work).all()
    data = {
        'title': 'Editions of %s' % work.title,
        'editions': editions,
        'work': work,
    }
    return TemplateResponse(request, 'editions.html', data)


def author_page(request, author_id):
    ''' landing page for an author '''
    try:
        author = models.Author.objects.get(id=author_id)
    except ValueError:
        return HttpResponseNotFound()

    if is_api_request(request):
        return JsonResponse(author.to_activity(), encoder=ActivityEncoder)

    books = models.Work.objects.filter(authors=author)
    data = {
        'title': author.name,
        'author': author,
        'books': [b.default_edition for b in books],
    }
    return TemplateResponse(request, 'author.html', data)


def tag_page(request, tag_id):
    ''' books related to a tag '''
    tag_obj = models.Tag.objects.filter(identifier=tag_id).first()
    if not tag_obj:
        return HttpResponseNotFound()

    if is_api_request(request):
        return JsonResponse(
            tag_obj.to_activity(**request.GET), encoder=ActivityEncoder)

    books = models.Edition.objects.filter(tag__identifier=tag_id).distinct()
    data = {
        'title': tag_obj.name,
        'books': books,
        'tag': tag_obj,
    }
    return TemplateResponse(request, 'tag.html', data)


def shelf_page(request, username, shelf_identifier):
    ''' display a shelf '''
    try:
        user = get_user_from_username(username)
    except models.User.DoesNotExist:
        return HttpResponseNotFound()

    shelf = models.Shelf.objects.get(user=user, identifier=shelf_identifier)

    if is_api_request(request):
        return JsonResponse(shelf.to_activity(**request.GET))

    return user_page(
        request, username, subpage='shelves', shelf=shelf_identifier)
